from __future__ import annotations

from pathlib import Path

from vynex_vpn_client.app_update import AppReleaseInfo
from vynex_vpn_client.app_updater import AppSelfUpdater, AppUpdateDownload
from vynex_vpn_client.constants import (
    APP_UPDATE_ASSET_NAME,
    APP_UPDATE_HELPER_SCRIPT_NAME,
    APP_VERSION,
)


class _FakeResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, *, chunk_size: int):  # noqa: ARG002
        return iter(self._chunks)


class _FakeSession:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.headers: dict[str, str] = {}

    def get(self, url: str, *, stream: bool, timeout: int):  # noqa: ARG002
        return _FakeResponse(self._chunks)


def _release_info() -> AppReleaseInfo:
    return AppReleaseInfo(
        current_version=APP_VERSION,
        latest_version="v99.0.0",
        release_url="https://github.com/ALFiX01/Vynex/releases/tag/v99.0.0",
        published_at="2026-04-15T10:00:00Z",
        release_notes="Release notes",
        asset_name=APP_UPDATE_ASSET_NAME,
        asset_download_url="https://example.com/VynexVPNClient.exe",
        asset_size=11,
        is_update_available=True,
    )


def test_download_release_stages_exe_via_part_file(tmp_path: Path) -> None:
    updater = AppSelfUpdater(
        updates_dir=tmp_path / "updates",
        session=_FakeSession([b"hello ", b"world"]),
    )

    download = updater.download_release(_release_info())

    assert download.staged_executable.name == "VynexVPNClient-v99.0.0.exe"
    assert download.staged_executable.read_bytes() == b"hello world"
    assert download.expected_size == 11
    assert not download.staged_executable.with_suffix(".exe.part").exists()


def test_prepare_apply_plan_builds_backup_and_helper_paths(tmp_path: Path) -> None:
    updates_dir = tmp_path / "updates"
    updater = AppSelfUpdater(updates_dir=updates_dir)
    staged_executable = updates_dir / "VynexVPNClient-v99.0.0.exe"
    staged_executable.parent.mkdir(parents=True, exist_ok=True)
    staged_executable.write_bytes(b"new exe")
    current_executable = tmp_path / "dist" / APP_UPDATE_ASSET_NAME
    current_executable.parent.mkdir(parents=True, exist_ok=True)
    current_executable.write_bytes(b"old exe")
    download = AppUpdateDownload(release=_release_info(), staged_executable=staged_executable, expected_size=7)

    plan = updater.prepare_apply_plan(download, current_pid=4321, current_executable=current_executable)

    assert plan.current_pid == 4321
    assert plan.current_executable == current_executable.resolve()
    assert plan.staged_executable == staged_executable.resolve()
    assert plan.backup_executable == current_executable.resolve().with_name("VynexVPNClient.old.exe")
    assert plan.helper_script == updates_dir / f"{Path(APP_UPDATE_HELPER_SCRIPT_NAME).stem}-4321{Path(APP_UPDATE_HELPER_SCRIPT_NAME).suffix}"


def test_generate_helper_script_includes_expected_paths_and_commands(tmp_path: Path) -> None:
    updates_dir = tmp_path / "updates"
    updater = AppSelfUpdater(updates_dir=updates_dir)
    staged_executable = updates_dir / "VynexVPNClient-v99.0.0.exe"
    staged_executable.parent.mkdir(parents=True, exist_ok=True)
    staged_executable.write_bytes(b"new exe")
    current_executable = tmp_path / "dist" / APP_UPDATE_ASSET_NAME
    current_executable.parent.mkdir(parents=True, exist_ok=True)
    current_executable.write_bytes(b"old exe")
    download = AppUpdateDownload(release=_release_info(), staged_executable=staged_executable, expected_size=7)
    plan = updater.prepare_apply_plan(download, current_pid=4321, current_executable=current_executable)

    script = updater.generate_helper_script(plan)

    assert 'set "CURRENT_PID=4321"' in script
    assert f'set "TARGET_EXE={current_executable.resolve()}"' in script
    assert f'set "STAGED_EXE={staged_executable.resolve()}"' in script
    assert 'move /Y "%TARGET_EXE%" "%BACKUP_EXE%"' in script
    assert 'move /Y "%STAGED_EXE%" "%TARGET_EXE%"' in script
    assert 'start "" "%TARGET_EXE%"' in script
    assert 'call :restore_backup' in script
