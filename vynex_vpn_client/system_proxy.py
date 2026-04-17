from __future__ import annotations

import ctypes
import winreg
from dataclasses import asdict, dataclass
from typing import Any

from .constants import LOCAL_PROXY_HOST


INTERNET_OPTION_REFRESH = 37
INTERNET_OPTION_SETTINGS_CHANGED = 39
INTERNET_SETTINGS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


@dataclass
class SystemProxyState:
    proxy_enable: int = 0
    proxy_server: str = ""
    proxy_override: str = ""
    auto_config_url: str = ""
    auto_detect: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SystemProxyState | None":
        if not data:
            return None
        return cls(**data)


class WindowsSystemProxyManager:
    def snapshot(self) -> SystemProxyState:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS_KEY, 0, winreg.KEY_READ) as key:
            return SystemProxyState(
                proxy_enable=self._read_dword(key, "ProxyEnable", 0),
                proxy_server=self._read_string(key, "ProxyServer", ""),
                proxy_override=self._read_string(key, "ProxyOverride", ""),
                auto_config_url=self._read_string(key, "AutoConfigURL", ""),
                auto_detect=self._read_dword(key, "AutoDetect", 0),
            )

    def enable_proxy(self, *, http_port: int, socks_port: int | None = None) -> None:
        previous_state = self.snapshot()
        proxy_entries = [
            f"http={LOCAL_PROXY_HOST}:{http_port}",
            f"https={LOCAL_PROXY_HOST}:{http_port}",
        ]
        if socks_port is not None:
            proxy_entries.append(f"socks={LOCAL_PROXY_HOST}:{socks_port}")
        self._apply_state(
            SystemProxyState(
                proxy_enable=1,
                proxy_server=";".join(proxy_entries),
                proxy_override="<local>",
                auto_config_url="",
                auto_detect=previous_state.auto_detect,
            ),
            rollback_state=previous_state,
        )

    def restore(self, state: SystemProxyState | None) -> None:
        if state is None:
            self.disable_proxy()
            return
        self._apply_state(state)

    def disable_proxy(self) -> None:
        current_state = self.snapshot()
        self._apply_state(
            SystemProxyState(
                proxy_enable=0,
                proxy_server="",
                proxy_override=current_state.proxy_override,
                auto_config_url=current_state.auto_config_url,
                auto_detect=current_state.auto_detect,
            )
        )

    def _apply_state(
        self,
        state: SystemProxyState,
        *,
        rollback_state: SystemProxyState | None = None,
    ) -> None:
        try:
            self._write_state(state)
            self._broadcast_settings_changed()
        except Exception:
            if rollback_state is not None:
                self._rollback_state(rollback_state)
            raise

    def _rollback_state(self, state: SystemProxyState) -> None:
        try:
            self._write_state(state)
            self._broadcast_settings_changed()
        except Exception:
            pass

    @staticmethod
    def _write_state(state: SystemProxyState) -> None:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            INTERNET_SETTINGS_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, int(state.proxy_enable))
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, state.proxy_server)
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, state.proxy_override)
            winreg.SetValueEx(key, "AutoConfigURL", 0, winreg.REG_SZ, state.auto_config_url)
            winreg.SetValueEx(key, "AutoDetect", 0, winreg.REG_DWORD, int(state.auto_detect))

    @staticmethod
    def is_vynex_managed_state(state: SystemProxyState | None) -> bool:
        if state is None or not int(state.proxy_enable):
            return False
        proxy_server = str(state.proxy_server or "").strip()
        if not proxy_server:
            return False
        protocols: set[str] = set()
        for raw_entry in proxy_server.split(";"):
            entry = raw_entry.strip()
            if not entry or "=" not in entry:
                return False
            protocol, address = (part.strip().lower() for part in entry.split("=", 1))
            if protocol not in {"http", "https", "socks"}:
                return False
            if ":" not in address:
                return False
            host, port_text = address.rsplit(":", 1)
            if host != LOCAL_PROXY_HOST or not port_text.isdigit():
                return False
            protocols.add(protocol)
        return {"http", "https"}.issubset(protocols)

    @staticmethod
    def _read_dword(key: int, name: str, default: int) -> int:
        try:
            value, _ = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return default
        return int(value)

    @staticmethod
    def _read_string(key: int, name: str, default: str) -> str:
        try:
            value, _ = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return default
        return str(value)

    @staticmethod
    def _broadcast_settings_changed() -> None:
        internet_set_option = ctypes.windll.wininet.InternetSetOptionW
        if not internet_set_option(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0):
            raise OSError("Не удалось уведомить Windows об изменении proxy-настроек.")
        if not internet_set_option(0, INTERNET_OPTION_REFRESH, 0, 0):
            raise OSError("Не удалось обновить proxy-настройки Windows.")
