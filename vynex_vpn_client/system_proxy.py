from __future__ import annotations

import ctypes
import winreg
from dataclasses import asdict, dataclass
from typing import Any


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
        proxy_entries = [
            f"http=127.0.0.1:{http_port}",
            f"https=127.0.0.1:{http_port}",
        ]
        if socks_port is not None:
            proxy_entries.append(f"socks=127.0.0.1:{socks_port}")
        proxy_server = ";".join(proxy_entries)
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            INTERNET_SETTINGS_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, proxy_server)
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "<local>")
            winreg.SetValueEx(key, "AutoConfigURL", 0, winreg.REG_SZ, "")
        self._broadcast_settings_changed()

    def restore(self, state: SystemProxyState | None) -> None:
        if state is None:
            self.disable_proxy()
            return
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
        self._broadcast_settings_changed()

    def disable_proxy(self) -> None:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            INTERNET_SETTINGS_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, "")
        self._broadcast_settings_changed()

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
