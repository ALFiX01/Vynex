from __future__ import annotations

import base64
import ctypes
import socket
from urllib.parse import unquote


def decode_base64(data: str) -> str:
    cleaned = data.strip()
    padding = "=" * (-len(cleaned) % 4)
    try:
        return base64.urlsafe_b64decode((cleaned + padding).encode("utf-8")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Не удалось декодировать Base64 данные.") from exc


def url_decode(value: str | None) -> str | None:
    return unquote(value) if value else value


def is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    process_handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not process_handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(process_handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == 259
    finally:
        ctypes.windll.kernel32.CloseHandle(process_handle)


def is_port_available(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) != 0


def clamp_port(port: int) -> int:
    if not 1 <= port <= 65535:
        raise ValueError("Порт должен быть в диапазоне 1..65535.")
    return port
