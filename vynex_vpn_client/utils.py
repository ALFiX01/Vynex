from __future__ import annotations

import base64
import ctypes
from dataclasses import dataclass
import ipaddress
import json
import secrets
import socket
import string
import subprocess
import time
from urllib.parse import unquote

import psutil

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


@dataclass(frozen=True)
class RunningProcessDetails:
    pid: int
    name: str
    executable_path: str | None = None


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


def list_running_processes_by_names(names: list[str] | tuple[str, ...] | set[str]) -> list[RunningProcessDetails]:
    expected_names = {str(name).strip().casefold() for name in names if str(name).strip()}
    if not expected_names:
        return []
    matches: list[RunningProcessDetails] = []
    for process in psutil.process_iter(["pid", "name", "exe"]):
        try:
            raw_name = str(process.info.get("name") or "").strip()
            if not raw_name or raw_name.casefold() not in expected_names:
                continue
            raw_pid = int(process.info["pid"])
            raw_path = str(process.info.get("exe") or "").strip() or None
        except (KeyError, TypeError, ValueError, psutil.Error, OSError):
            continue
        matches.append(
            RunningProcessDetails(
                pid=raw_pid,
                name=raw_name,
                executable_path=raw_path,
            )
        )
    matches.sort(key=lambda item: (item.name.casefold(), item.pid))
    return matches


def terminate_running_processes(
    processes: list[RunningProcessDetails] | tuple[RunningProcessDetails, ...],
    *,
    timeout: float = 3.0,
    kill_timeout: float = 2.0,
) -> list[RunningProcessDetails]:
    if not processes:
        return []

    managed_processes: list[psutil.Process] = []
    process_info_by_pid: dict[int, RunningProcessDetails] = {}
    failed_by_pid: dict[int, RunningProcessDetails] = {}

    for process_info in processes:
        try:
            process = psutil.Process(process_info.pid)
            actual_name = str(process.name() or "").strip()
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
            failed_by_pid[process_info.pid] = process_info
            continue
        if actual_name and actual_name.casefold() != process_info.name.casefold():
            continue
        managed_processes.append(process)
        process_info_by_pid[process.pid] = process_info

    pending_processes: list[psutil.Process] = []
    for process in managed_processes:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
            failed_by_pid[process.pid] = process_info_by_pid[process.pid]
            continue
        pending_processes.append(process)

    gone, alive = psutil.wait_procs(pending_processes, timeout=max(timeout, 0.0))
    del gone
    retry_processes: list[psutil.Process] = []
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
            failed_by_pid[process.pid] = process_info_by_pid[process.pid]
            continue
        retry_processes.append(process)

    _, still_alive = psutil.wait_procs(retry_processes, timeout=max(kill_timeout, 0.0))
    for process in still_alive:
        failed_by_pid[process.pid] = process_info_by_pid[process.pid]

    return sorted(failed_by_pid.values(), key=lambda item: (item.name.casefold(), item.pid))


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


def _json_string_list(payload: object | None) -> tuple[str, ...]:
    if payload is None:
        return ()
    items = payload if isinstance(payload, list) else [payload]
    values: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in values:
            values.append(text)
    return tuple(values)


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
    details = _ready_tun_interface_details(interface_name, require_route=require_route)
    return details is not None


def wait_for_tun_interface_details(
    interface_name: str,
    *,
    timeout: float = 12.0,
    interval: float = 0.2,
    require_route: bool = False,
) -> WindowsInterfaceDetails | None:
    deadline = time.monotonic() + timeout
    poll_interval = max(0.05, float(interval))
    while True:
        details = _ready_tun_interface_details(interface_name, require_route=require_route)
        if details is not None:
            return details
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval, remaining))
        poll_interval = min(poll_interval * 1.5, 0.75)
    return _ready_tun_interface_details(interface_name, require_route=require_route)


def _ready_tun_interface_details(
    interface_name: str,
    *,
    require_route: bool,
) -> WindowsInterfaceDetails | None:
    details = get_interface_details(interface_name, allow_link_local=True)
    if details is None:
        return None
    if str(details.status or "").lower() != "up":
        return None
    if not details.ipv4:
        return None
    if require_route and not details.has_route:
        return None
    return details


def wait_for_tun_interface(
    interface_name: str,
    *,
    timeout: float = 12.0,
    interval: float = 0.2,
    require_route: bool = False,
) -> bool:
    return (
        wait_for_tun_interface_details(
            interface_name,
            timeout=timeout,
            interval=interval,
            require_route=require_route,
        )
        is not None
    )


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


def get_interface_ipv4_addresses(
    interface_name: str,
    *,
    allow_link_local: bool = True,
) -> tuple[str, ...]:
    escaped_name = interface_name.replace("'", "''")
    ip_filter = "$true" if allow_link_local else "$_.IPAddress -notlike '169.254.*'"
    command = (
        f"Get-NetIPAddress -InterfaceAlias '{escaped_name}' -AddressFamily IPv4 "
        "-ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.IPAddress -ne '127.0.0.1' -and {ip_filter} }} | "
        "Select-Object -ExpandProperty IPAddress | ConvertTo-Json -Compress"
    )
    return _json_string_list(_run_powershell_json(command))


def get_interface_dns_servers(interface_name: str) -> tuple[str, ...]:
    escaped_name = interface_name.replace("'", "''")
    command = (
        f"Get-DnsClientServerAddress -InterfaceAlias '{escaped_name}' -AddressFamily IPv4 "
        "-ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty ServerAddresses | ConvertTo-Json -Compress"
    )
    return _json_string_list(_run_powershell_json(command))


def get_interface_ipv4_route_prefixes(
    *,
    interface_name: str | None = None,
    interface_index: int | None = None,
) -> tuple[str, ...]:
    filters = ["-AddressFamily IPv4"]
    if interface_name is not None:
        filters.append(f"-InterfaceAlias {_single_quoted_powershell(interface_name)}")
    if interface_index is not None:
        filters.append(f"-InterfaceIndex {int(interface_index)}")
    command = (
        f"Get-NetRoute {' '.join(filters)} -ErrorAction SilentlyContinue | "
        "Where-Object { $_.DestinationPrefix -and $_.State -ne 'Invalid' } | "
        "Select-Object -ExpandProperty DestinationPrefix | Sort-Object -Unique | "
        "ConvertTo-Json -Compress"
    )
    return _json_string_list(_run_powershell_json(command))


def reset_interface_dns_servers(interface_name: str) -> None:
    escaped_name = interface_name.replace("'", "''")
    command = (
        f"Set-DnsClientServerAddress -InterfaceAlias '{escaped_name}' "
        "-ResetServerAddresses -ErrorAction SilentlyContinue | Out-Null"
    )
    _run_powershell(command)


def remove_interface_ipv4_addresses(interface_name: str, addresses: list[str] | tuple[str, ...]) -> None:
    escaped_name = interface_name.replace("'", "''")
    for raw_address in addresses:
        try:
            address = ipaddress.ip_interface(str(raw_address).strip())
        except ValueError:
            continue
        if address.version != 4:
            continue
        command = (
            f"Remove-NetIPAddress -InterfaceAlias '{escaped_name}' "
            f"-IPAddress {_single_quoted_powershell(str(address.ip))} "
            f"-PrefixLength {int(address.network.prefixlen)} "
            "-Confirm:$false -ErrorAction SilentlyContinue | Out-Null"
        )
        _run_powershell(command)
