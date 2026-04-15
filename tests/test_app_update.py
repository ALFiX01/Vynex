from __future__ import annotations

from vynex_vpn_client.app_update import AppReleaseInfo, AppUpdateChecker
from vynex_vpn_client.constants import APP_RELEASES_PAGE, APP_UPDATE_ASSET_NAME, APP_VERSION


def _newer_version() -> str:
    return "v99.0.0"


def _release_payload(*, version: str | None = None, assets: list[dict] | None = None) -> dict:
    return {
        "tag_name": version or _newer_version(),
        "html_url": "https://github.com/ALFiX01/Vynex/releases/tag/test",
        "published_at": "2026-04-15T10:00:00Z",
        "body": "Bug fixes and runtime improvements.",
        "assets": assets or [],
    }


def test_parse_release_payload_prefers_exact_app_exe_asset() -> None:
    payload = _release_payload(
        assets=[
            {
                "name": "OtherClient.exe",
                "browser_download_url": "https://example.com/other.exe",
                "size": 10,
            },
            {
                "name": APP_UPDATE_ASSET_NAME,
                "browser_download_url": "https://example.com/VynexVPNClient.exe",
                "size": 42,
            },
        ]
    )

    release_info = AppUpdateChecker.parse_release_payload(payload, checked_at=123.0)

    assert release_info.latest_version == _newer_version()
    assert release_info.release_url == "https://github.com/ALFiX01/Vynex/releases/tag/test"
    assert release_info.published_at == "2026-04-15T10:00:00Z"
    assert release_info.release_notes == "Bug fixes and runtime improvements."
    assert release_info.asset_name == APP_UPDATE_ASSET_NAME
    assert release_info.asset_download_url == "https://example.com/VynexVPNClient.exe"
    assert release_info.asset_size == 42
    assert release_info.is_update_available is True
    assert release_info.error is None


def test_parse_release_payload_falls_back_to_first_exe_asset() -> None:
    payload = _release_payload(
        assets=[
            {
                "name": "portable-build.exe",
                "browser_download_url": "https://example.com/portable.exe",
                "size": 64,
            },
            {
                "name": "notes.txt",
                "browser_download_url": "https://example.com/notes.txt",
                "size": 3,
            },
        ]
    )

    release_info = AppUpdateChecker.parse_release_payload(payload)

    assert release_info.asset_name == "portable-build.exe"
    assert release_info.asset_download_url == "https://example.com/portable.exe"
    assert release_info.asset_size == 64
    assert release_info.is_update_available is True


def test_parse_release_payload_reports_missing_exe_asset() -> None:
    payload = _release_payload(
        assets=[
            {
                "name": "Vynex.zip",
                "browser_download_url": "https://example.com/Vynex.zip",
                "size": 120,
            }
        ]
    )

    release_info = AppUpdateChecker.parse_release_payload(payload)

    assert release_info.latest_version == _newer_version()
    assert release_info.asset_name is None
    assert release_info.asset_download_url is None
    assert release_info.is_update_available is False
    assert release_info.error == "В latest release не найден exe-asset приложения."


def test_parse_release_payload_detects_no_update_for_current_version() -> None:
    payload = _release_payload(
        version=f"v{APP_VERSION}",
        assets=[
            {
                "name": APP_UPDATE_ASSET_NAME,
                "browser_download_url": "https://example.com/VynexVPNClient.exe",
                "size": 512,
            }
        ],
    )

    release_info = AppUpdateChecker.parse_release_payload(payload)

    assert release_info.asset_name == APP_UPDATE_ASSET_NAME
    assert release_info.is_update_available is False


def test_release_info_round_trip_preserves_extended_fields() -> None:
    release_info = AppReleaseInfo(
        current_version=APP_VERSION,
        latest_version=_newer_version(),
        release_url=APP_RELEASES_PAGE,
        published_at="2026-04-15T10:00:00Z",
        release_notes="Notes",
        asset_name=APP_UPDATE_ASSET_NAME,
        asset_download_url="https://example.com/VynexVPNClient.exe",
        asset_size=1024,
        is_update_available=True,
        checked_at=123.0,
    )

    restored = AppReleaseInfo.from_dict(release_info.to_dict())

    assert restored == release_info
