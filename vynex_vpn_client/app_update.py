from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time

import requests

from .constants import (
    APP_RELEASES_API,
    APP_RELEASES_PAGE,
    APP_UPDATE_ASSET_NAME,
    APP_UPDATE_CACHE_FILE,
    APP_UPDATE_CHECK_TTL_SECONDS,
    APP_UPDATE_REQUEST_TIMEOUT_SECONDS,
    APP_VERSION,
)


@dataclass(frozen=True)
class AppReleaseInfo:
    current_version: str
    latest_version: str | None = None
    release_url: str | None = None
    published_at: str | None = None
    release_notes: str | None = None
    asset_name: str | None = None
    asset_download_url: str | None = None
    asset_size: int | None = None
    is_update_available: bool = False
    error: str | None = None
    checked_at: float | None = None

    @property
    def has_installable_asset(self) -> bool:
        return bool(self.asset_name and self.asset_download_url)

    def to_dict(self) -> dict[str, object]:
        return {
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "release_url": self.release_url,
            "published_at": self.published_at,
            "release_notes": self.release_notes,
            "asset_name": self.asset_name,
            "asset_download_url": self.asset_download_url,
            "asset_size": self.asset_size,
            "is_update_available": self.is_update_available,
            "error": self.error,
            "checked_at": self.checked_at,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "AppReleaseInfo":
        asset_size = payload.get("asset_size")
        return cls(
            current_version=str(payload.get("current_version") or APP_VERSION),
            latest_version=str(payload["latest_version"]) if payload.get("latest_version") else None,
            release_url=str(payload["release_url"]) if payload.get("release_url") else None,
            published_at=str(payload["published_at"]) if payload.get("published_at") else None,
            release_notes=str(payload["release_notes"]) if payload.get("release_notes") else None,
            asset_name=str(payload["asset_name"]) if payload.get("asset_name") else None,
            asset_download_url=str(payload["asset_download_url"]) if payload.get("asset_download_url") else None,
            asset_size=int(asset_size) if asset_size not in (None, "") else None,
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
        if cached.latest_version and not cached.asset_download_url and not cached.error:
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
            response = self.session.get(APP_RELEASES_API, timeout=APP_UPDATE_REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
            release_info = self.parse_release_payload(payload, checked_at=time.time())
        except (requests.RequestException, TypeError, ValueError) as exc:
            cached = self.get_cached_release(max_age_seconds=None)
            if cached is not None:
                return cached
            return self._normalize_release_info(
                AppReleaseInfo(
                    current_version=APP_VERSION,
                    error=str(exc),
                    checked_at=time.time(),
                )
            )

        self._save_cached_release(release_info)
        return release_info

    @classmethod
    def parse_release_payload(cls, payload: dict, *, checked_at: float | None = None) -> AppReleaseInfo:
        if not isinstance(payload, dict):
            raise TypeError("GitHub Releases API вернул некорректный ответ.")
        latest_version = cls._release_version_label(payload)
        release_url = str(payload.get("html_url") or APP_RELEASES_PAGE)
        published_at = str(payload.get("published_at") or "").strip() or None
        release_notes = str(payload.get("body") or "").strip() or None
        asset_name: str | None = None
        asset_download_url: str | None = None
        asset_size: int | None = None
        error: str | None = None

        try:
            asset = cls._select_release_asset(payload.get("assets"))
        except ValueError as exc:
            error = str(exc)
        else:
            asset_name = asset["name"]
            asset_download_url = asset["download_url"]
            asset_size = asset["size"]

        return cls._normalize_release_info(
            AppReleaseInfo(
                current_version=APP_VERSION,
                latest_version=latest_version,
                release_url=release_url,
                published_at=published_at,
                release_notes=release_notes,
                asset_name=asset_name,
                asset_download_url=asset_download_url,
                asset_size=asset_size,
                error=error,
                checked_at=checked_at,
            )
        )

    @staticmethod
    def _normalize_release_info(release_info: AppReleaseInfo) -> AppReleaseInfo:
        current_key = AppUpdateChecker._version_key(APP_VERSION)
        latest_key = AppUpdateChecker._version_key(release_info.latest_version or "")
        has_installable_asset = release_info.has_installable_asset and not release_info.error
        is_update_available = bool(
            release_info.latest_version
            and current_key is not None
            and latest_key is not None
            and latest_key > current_key
            and has_installable_asset
        )
        return AppReleaseInfo(
            current_version=APP_VERSION,
            latest_version=release_info.latest_version,
            release_url=release_info.release_url or APP_RELEASES_PAGE,
            published_at=release_info.published_at,
            release_notes=release_info.release_notes,
            asset_name=release_info.asset_name,
            asset_download_url=release_info.asset_download_url,
            asset_size=release_info.asset_size,
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
    def _select_release_asset(assets_payload: object) -> dict[str, str | int | None]:
        assets = assets_payload if isinstance(assets_payload, list) else []
        exe_assets: list[dict[str, str | int | None]] = []
        for asset_payload in assets:
            if not isinstance(asset_payload, dict):
                continue
            name = str(asset_payload.get("name") or "").strip()
            if not name or not name.lower().endswith(".exe"):
                continue
            download_url = str(asset_payload.get("browser_download_url") or "").strip()
            size_value = asset_payload.get("size")
            size = int(size_value) if isinstance(size_value, (int, float)) else None
            exe_assets.append(
                {
                    "name": name,
                    "download_url": download_url or None,
                    "size": size if size is not None and size >= 0 else None,
                }
            )
        if not exe_assets:
            raise ValueError("В latest release не найден exe-asset приложения.")
        exact_match = next(
            (asset for asset in exe_assets if str(asset["name"]).lower() == APP_UPDATE_ASSET_NAME.lower()),
            None,
        )
        selected = exact_match or exe_assets[0]
        if not selected["download_url"]:
            raise ValueError(f"Для asset '{selected['name']}' отсутствует ссылка на скачивание.")
        return selected

    @staticmethod
    def _version_key(value: str) -> tuple[int, ...] | None:
        numbers = tuple(int(part) for part in re.findall(r"\d+", value))
        if not numbers:
            return None
        trimmed = list(numbers)
        while len(trimmed) > 1 and trimmed[-1] == 0:
            trimmed.pop()
        return tuple(trimmed)
