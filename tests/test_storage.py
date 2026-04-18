from __future__ import annotations

from pathlib import Path

import pytest

import vynex_vpn_client.storage as storage_module
from vynex_vpn_client.models import AppSettings, RuntimeState
from vynex_vpn_client.storage import JsonStorage, StorageCorruptionError


def _configure_storage_paths(tmp_path: Path, monkeypatch) -> Path:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(storage_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage_module, "LEGACY_DATA_DIR", tmp_path / "legacy")
    monkeypatch.setattr(storage_module, "ROUTING_PROFILES_DIR", data_dir / "routing_profiles")
    monkeypatch.setattr(storage_module, "SERVERS_FILE", data_dir / "servers.json")
    monkeypatch.setattr(storage_module, "SUBSCRIPTIONS_FILE", data_dir / "subscriptions.json")
    monkeypatch.setattr(storage_module, "RUNTIME_STATE_FILE", data_dir / "runtime_state.json")
    monkeypatch.setattr(storage_module, "SETTINGS_FILE", data_dir / "settings.json")
    return data_dir


def test_load_runtime_state_restores_from_backup_after_corruption(tmp_path: Path, monkeypatch) -> None:
    data_dir = _configure_storage_paths(tmp_path, monkeypatch)
    storage = JsonStorage()
    state = RuntimeState(
        pid=1234,
        mode="PROXY",
        server_id="server-1",
        system_proxy_enabled=True,
    )
    storage.save_runtime_state(state)
    runtime_state_path = data_dir / "runtime_state.json"
    runtime_state_path.write_text("{broken", encoding="utf-8")

    restored = storage.load_runtime_state()

    assert restored == state
    assert '"pid": 1234' in runtime_state_path.read_text(encoding="utf-8")


def test_load_runtime_state_raises_when_primary_and_backup_are_corrupt(tmp_path: Path, monkeypatch) -> None:
    data_dir = _configure_storage_paths(tmp_path, monkeypatch)
    storage = JsonStorage()
    storage.save_runtime_state(RuntimeState(pid=55))
    runtime_state_path = data_dir / "runtime_state.json"
    backup_path = runtime_state_path.with_name("runtime_state.json.bak")
    runtime_state_path.write_text("{broken", encoding="utf-8")
    backup_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(StorageCorruptionError):
        storage.load_runtime_state()


def test_settings_persist_auto_update_subscriptions_on_startup(tmp_path: Path, monkeypatch) -> None:
    _configure_storage_paths(tmp_path, monkeypatch)
    storage = JsonStorage()

    assert storage.load_settings() == AppSettings()

    updated = AppSettings(auto_update_subscriptions_on_startup=True)
    storage.save_settings(updated)

    assert storage.load_settings() == updated
