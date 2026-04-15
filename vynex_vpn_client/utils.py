from __future__ import annotations

import base64
import ctypes
from dataclasses import dataclass
import json
import secrets
import socket
import string
import subprocess
import time
from urllib.parse import unquote

from .constants import LOCAL_PROXY_HOST

RUNTIME_PORT_MIN = 20000
RUNTIME_PORT_MAX = 60000


@dataclass(frozen=True)
class WindowsInterfaceDetails:
    alias: str
    index: int
    ipv4: str | None = None
    status: str | None = None
    gateway: str | None = None
    has_route: bool = False


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


def is_running_as_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


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


def _powershell_creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _powershell_utf8_command(command: str) -> str:
    return (
        "[Console]::InputEncoding = [System.Text.Encoding]::UTF8; "
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$OutputEncoding = [System.Text.Encoding]::UTF8; "
        + command
    )


def _run_powershell(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", _powershell_utf8_command(command)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        creationflags=_powershell_creationflags(),
    )


def _single_quoted_powershell(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _parse_interface_details(payload: object) -> WindowsInterfaceDetails | None:
    if not isinstance(payload, dict):
        return None
    alias = str(payload.get("alias") or "").strip()
    if not alias:
        return None
    raw_index = payload.get("index")
    try:
        index = int(raw_index)
    except (TypeError, ValueError):
        return None
    return WindowsInterfaceDetails(
        alias=alias,
        index=index,
        ipv4=str(payload.get("ipv4") or "").strip() or None,
        status=str(payload.get("status") or "").strip() or None,
        gateway=str(payload.get("gateway") or "").strip() or None,
        has_route=bool(payload.get("has_route", False)),
    )


def _run_powershell_json(command: str) -> object | None:
    result = _run_powershell(command)
    if result.returncode != 0:
        return None
    raw_output = result.stdout.strip()
    if not raw_output:
        return None
    try:
        return json.loads(raw_output)
    except json.JSONDecodeError:
        return None


def get_active_ipv4_interface(*, exclude_aliases: set[str] | None = None) -> WindowsInterfaceDetails | None:
    excluded = sorted(alias for alias in (exclude_aliases or set()) if alias)
    escaped_excluded = ", ".join(_single_quoted_powershell(alias) for alias in excluded)
    excluded_clause = f"$excluded = @({escaped_excluded}); " if excluded else "$excluded = @(); "
    command = (
        excluded_clause
        + "$route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -AddressFamily IPv4 "
        "-ErrorAction SilentlyContinue | "
        "Where-Object { $_.State -eq 'Alive' -and $_.NextHop -ne '0.0.0.0' -and $_.InterfaceAlias -notin $excluded } | "
        "Sort-Object RouteMetric, InterfaceMetric | Select-Object -First 1; "
        "if ($null -eq $route) { exit 1 }; "
        "$ip = Get-NetIPAddress -InterfaceIndex $route.InterfaceIndex -AddressFamily IPv4 "
        "-ErrorAction SilentlyContinue | "
        "Where-Object { $_.IPAddress -ne '127.0.0.1' -and $_.IPAddress -notlike '169.254.*' } | "
        "Select-Object -First 1; "
        "[pscustomobject]@{"
        "alias = $route.InterfaceAlias; "
        "index = [int]$route.InterfaceIndex; "
        "ipv4 = if ($ip) { $ip.IPAddress } else { $null }; "
        "status = 'Up'; "
        "gateway = $route.NextHop; "
        "has_route = $true"
        "} | ConvertTo-Json -Compress"
    )
    return _parse_interface_details(_run_powershell_json(command))


def get_interface_details(
    interface_name: str,
    *,
    allow_link_local: bool = True,
) -> WindowsInterfaceDetails | None:
    escaped_name = interface_name.replace("'", "''")
    ip_filter = "$true" if allow_link_local else "$_.IPAddress -notlike '169.254.*'"
    command = (
        f"$adapter = Get-NetAdapter -Name '{escaped_name}' -ErrorAction SilentlyContinue; "
        "if ($null -eq $adapter) { exit 1 }; "
        f"$ip = Get-NetIPAddress -InterfaceAlias '{escaped_name}' -AddressFamily IPv4 "
        "-ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.IPAddress -ne '127.0.0.1' -and {ip_filter} }} | "
        "Select-Object -First 1; "
        f"$route = Get-NetRoute -InterfaceAlias '{escaped_name}' -AddressFamily IPv4 "
        "-ErrorAction SilentlyContinue | Select-Object -First 1; "
        "[pscustomobject]@{"
        "alias = $adapter.Name; "
        "index = [int]$adapter.ifIndex; "
        "ipv4 = if ($ip) { $ip.IPAddress } else { $null }; "
        "status = [string]$adapter.Status; "
        "gateway = $null; "
        "has_route = [bool]($route)"
        "} | ConvertTo-Json -Compress"
    )
    return _parse_interface_details(_run_powershell_json(command))


def is_tun_interface_ready(interface_name: str, *, require_route: bool = False) -> bool:
    details = get_interface_details(interface_name, allow_link_local=True)
    if details is None:
        return False
    if str(details.status or "").lower() != "up":
        return False
    if not details.ipv4:
        return False
    if require_route and not details.has_route:
        return False
    return True


def wait_for_tun_interface(
    interface_name: str,
    *,
    timeout: float = 12.0,
    interval: float = 0.2,
    require_route: bool = False,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_tun_interface_ready(interface_name, require_route=require_route):
            return True
        time.sleep(interval)
    return is_tun_interface_ready(interface_name, require_route=require_route)


def add_ipv4_route(
    destination_prefix: str,
    *,
    interface_index: int,
    next_hop: str,
    route_metric: int = 1,
) -> None:
    prefix = _single_quoted_powershell(destination_prefix)
    gateway = _single_quoted_powershell(next_hop)
    command = (
        f"$existing = Get-NetRoute -DestinationPrefix {prefix} -AddressFamily IPv4 "
        f"-InterfaceIndex {int(interface_index)} -ErrorAction SilentlyContinue; "
        "if ($existing) { $existing | Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue }; "
        f"New-NetRoute -DestinationPrefix {prefix} -InterfaceIndex {int(interface_index)} "
        f"-NextHop {gateway} -AddressFamily IPv4 -RouteMetric {int(route_metric)} "
        "-PolicyStore ActiveStore -ErrorAction Stop | Out-Null"
    )
    result = _run_powershell(command)
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            f"Не удалось добавить маршрут {destination_prefix} через TUN интерфейс. {stderr}".strip()
        )


def remove_ipv4_route(
    destination_prefix: str,
    *,
    interface_index: int | None = None,
    next_hop: str | None = None,
) -> None:
    filters = [f"-DestinationPrefix {_single_quoted_powershell(destination_prefix)}", "-AddressFamily IPv4"]
    if interface_index is not None:
        filters.append(f"-InterfaceIndex {int(interface_index)}")
    command = f"$route = Get-NetRoute {' '.join(filters)} -ErrorAction SilentlyContinue"
    if next_hop:
        command += f" | Where-Object {{ $_.NextHop -eq {_single_quoted_powershell(next_hop)} }}"
    command += "; if ($route) { $route | Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue | Out-Null }"
    _run_powershell(command)
