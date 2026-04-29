from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import vynex_vpn_client.storage as storage_module
from vynex_vpn_client.models import AppSettings, RuntimeState, ServerEntry
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


def _make_server(name: str, *, host: str, port: int, raw_link: str) -> ServerEntry:
    return ServerEntry.new(
        name=name,
        protocol="vless",
        host=host,
        port=port,
        raw_link=raw_link,
        extra={"id": f"id-{name.lower()}"},
    )


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


def test_models_parse_string_boolean_fields_without_truthiness_trap() -> None:
    settings = AppSettings.from_dict(
        {
            "set_system_proxy": "false",
            "auto_update_subscriptions_on_startup": "false",
        }
    )
    runtime_state = RuntimeState.from_dict({"system_proxy_enabled": "false"})

    assert settings.set_system_proxy is False
    assert settings.auto_update_subscriptions_on_startup is False
    assert runtime_state.system_proxy_enabled is False


def test_upsert_servers_loads_and_saves_once_for_batch_import(tmp_path: Path, monkeypatch) -> None:
    _configure_storage_paths(tmp_path, monkeypatch)
    storage = JsonStorage()
    first = _make_server(
        "One",
        host="one.example.com",
        port=443,
        raw_link="vless://id-1@one.example.com:443#One",
    )
    second = _make_server(
        "Two",
        host="two.example.com",
        port=8443,
        raw_link="vless://id-2@two.example.com:8443#Two",
    )

    with (
        patch.object(storage, "load_servers", wraps=storage.load_servers) as load_servers,
        patch.object(storage, "save_servers", wraps=storage.save_servers) as save_servers,
    ):
        saved = storage.upsert_servers([first, second])

    assert [server.name for server in saved] == ["One", "Two"]
    assert load_servers.call_count == 1
    assert save_servers.call_count == 1
    assert [server.name for server in storage.load_servers()] == ["One", "Two"]
