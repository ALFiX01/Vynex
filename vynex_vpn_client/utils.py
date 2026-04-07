from __future__ import annotations

import base64
import ctypes
import secrets
import socket
import string
import subprocess
import time
from urllib.parse import unquote

from .constants import LOCAL_PROXY_HOST

RUNTIME_PORT_MIN = 20000
RUNTIME_PORT_MAX = 60000


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


def is_port_available(port: int, host: str = LOCAL_PROXY_HOST) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) != 0


def clamp_port(port: int) -> int:
    if not 1 <= port <= 65535:
        raise ValueError("Порт должен быть в диапазоне 1..65535.")
    return port


def pick_random_port(
    *,
    used_ports: set[int] | None = None,
    host: str = LOCAL_PROXY_HOST,
    min_port: int = RUNTIME_PORT_MIN,
    max_port: int = RUNTIME_PORT_MAX,
    attempts: int = 128,
) -> int:
    occupied = used_ports or set()
    if min_port > max_port:
        raise ValueError("Некорректный диапазон портов.")
    span = max_port - min_port + 1
    for _ in range(min(attempts, span)):
        candidate = min_port + secrets.randbelow(span)
        if candidate in occupied:
            continue
        if is_port_available(candidate, host=host):
            return candidate
    for candidate in range(min_port, max_port + 1):
        if candidate in occupied:
            continue
        if is_port_available(candidate, host=host):
            return candidate
    raise RuntimeError("Не удалось подобрать свободный локальный порт.")


def generate_random_username(length: int = 12) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_random_password(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def wait_for_port_listener(
    port: int,
    *,
    host: str = LOCAL_PROXY_HOST,
    timeout: float = 10.0,
    interval: float = 0.2,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_port_available(port, host=host):
            return True
        time.sleep(interval)
    return not is_port_available(port, host=host)


def is_tun_interface_ready(interface_name: str) -> bool:
    escaped_name = interface_name.replace("'", "''")
    command = (
        f"$adapter = Get-NetAdapter -Name '{escaped_name}' -ErrorAction SilentlyContinue; "
        f"$route = Get-NetRoute -InterfaceAlias '{escaped_name}' -AddressFamily IPv4 "
        "-ErrorAction SilentlyContinue | Select-Object -First 1; "
        "if ($null -eq $adapter -or $null -eq $route) { exit 1 }; "
        "if ($adapter.Status -eq 'Up') { exit 0 } else { exit 2 }"
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        creationflags=creationflags,
    )
    return result.returncode == 0


def wait_for_tun_interface(
    interface_name: str,
    *,
    timeout: float = 12.0,
    interval: float = 0.2,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_tun_interface_ready(interface_name):
            return True
        time.sleep(interval)
    return is_tun_interface_ready(interface_name)
