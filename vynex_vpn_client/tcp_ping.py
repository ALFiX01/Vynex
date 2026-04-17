from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import errno
import socket
from typing import Iterable
from time import perf_counter

from .constants import TCP_PING_MAX_CONCURRENCY, TCP_PING_TIMEOUT_SECONDS
from .models import ServerEntry, utc_now_iso

TCP_PING_UNSUPPORTED_ERROR = "udp only"


@dataclass(frozen=True)
class TcpPingResult:
    server_id: str
    ok: bool
    latency_ms: int | None
    error: str | None
    checked_at: str


class TcpPingService:
    def ping_server(
        self,
        server: ServerEntry,
        *,
        timeout: float = TCP_PING_TIMEOUT_SECONDS,
    ) -> TcpPingResult:
        server_id = str(getattr(server, "id", "") or "")
        if is_tcp_ping_unsupported_server(server):
            return self._failed_result(server_id, TCP_PING_UNSUPPORTED_ERROR)
        host = self._normalize_host(getattr(server, "host", ""))
        port = self._normalize_port(getattr(server, "port", None))
        if host is None:
            return self._failed_result(server_id, "invalid host")
        if port is None:
            return self._failed_result(server_id, "invalid port")

        started_at = perf_counter()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                latency_ms = max(1, int(round((perf_counter() - started_at) * 1000)))
                return TcpPingResult(
                    server_id=server_id,
                    ok=True,
                    latency_ms=latency_ms,
                    error=None,
                    checked_at=utc_now_iso(),
                )
        except socket.gaierror:
            return self._failed_result(server_id, "dns error")
        except (socket.timeout, TimeoutError):
            return self._failed_result(server_id, "timeout")
        except ConnectionRefusedError:
            return self._failed_result(server_id, "refused")
        except OSError as error:
            return self._failed_result(server_id, self._os_error_label(error))

    def ping_many(
        self,
        servers: Iterable[ServerEntry],
        *,
        timeout: float = TCP_PING_TIMEOUT_SECONDS,
        concurrency: int = TCP_PING_MAX_CONCURRENCY,
    ) -> list[TcpPingResult]:
        server_list = list(servers)
        if not server_list:
            return []

        max_workers = min(self._normalize_concurrency(concurrency), len(server_list))
        results: list[TcpPingResult | None] = [None] * len(server_list)
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="vynex-tcp-ping") as executor:
            future_map = {
                executor.submit(self.ping_server, server, timeout=timeout): index
                for index, server in enumerate(server_list)
            }
            for future in as_completed(future_map):
                index = future_map[future]
                server_id = str(getattr(server_list[index], "id", "") or "")
                try:
                    results[index] = future.result()
                except Exception:
                    results[index] = self._failed_result(server_id, "connect error")
        return [result for result in results if result is not None]

    @staticmethod
    def _failed_result(server_id: str, error: str) -> TcpPingResult:
        return TcpPingResult(
            server_id=server_id,
            ok=False,
            latency_ms=None,
            error=error,
            checked_at=utc_now_iso(),
        )

    @staticmethod
    def _normalize_host(value: object) -> str | None:
        normalized = str(value or "").strip()
        if normalized.startswith("[") and normalized.endswith("]"):
            normalized = normalized[1:-1].strip()
        return normalized or None

    @staticmethod
    def _normalize_port(value: object) -> int | None:
        try:
            port = int(value)
        except (TypeError, ValueError):
            return None
        if not 1 <= port <= 65535:
            return None
        return port

    @staticmethod
    def _normalize_concurrency(value: object) -> int:
        try:
            concurrency = int(value)
        except (TypeError, ValueError):
            return TCP_PING_MAX_CONCURRENCY
        return max(1, concurrency)

    @staticmethod
    def _os_error_label(error: OSError) -> str:
        codes = {error.errno, getattr(error, "winerror", None)}
        if any(code in {errno.ENETUNREACH, errno.EHOSTUNREACH, 10051, 10065} for code in codes if code is not None):
            return "unreachable"
        if any(code in {errno.ECONNRESET, 10054} for code in codes if code is not None):
            return "reset"
        if any(code in {errno.ECONNABORTED, 10053} for code in codes if code is not None):
            return "aborted"
        if any(code in {errno.EADDRNOTAVAIL, errno.EINVAL, 10049} for code in codes if code is not None):
            return "invalid address"
        if any(code in {errno.EACCES, 10013} for code in codes if code is not None):
            return "forbidden"
        return "connect error"


def sort_tcp_ping_results(
    servers: Iterable[ServerEntry],
    results: Iterable[TcpPingResult],
) -> list[tuple[ServerEntry, TcpPingResult]]:
    result_by_id = {result.server_id: result for result in results}
    paired_results = [
        (server, result_by_id[server.id])
        for server in servers
        if server.id in result_by_id
    ]
    return sorted(
        paired_results,
        key=lambda item: (
            _tcp_ping_sort_bucket(item[1]),
            item[1].latency_ms if item[1].ok and item[1].latency_ms is not None else 10**9,
            item[0].name.lower(),
            item[0].host.lower(),
            item[0].port,
        ),
    )


def is_tcp_ping_unsupported_server(server: ServerEntry) -> bool:
    protocol = str(getattr(server, "protocol", "")).strip().lower()
    return bool(getattr(server, "is_amneziawg", False)) or protocol in {"amneziawg", "hy2", "hysteria2"}


def is_tcp_ping_unsupported_result(result: TcpPingResult) -> bool:
    return result.error == TCP_PING_UNSUPPORTED_ERROR


def _tcp_ping_sort_bucket(result: TcpPingResult) -> int:
    if result.ok:
        return 0
    if is_tcp_ping_unsupported_result(result):
        return 1
    return 2
