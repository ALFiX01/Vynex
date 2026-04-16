from __future__ import annotations

from contextlib import contextmanager
import socket
import socketserver
import threading
from unittest.mock import patch

from vynex_vpn_client.models import ServerEntry
from vynex_vpn_client.tcp_ping import (
    TCP_PING_UNSUPPORTED_ERROR,
    TcpPingResult,
    TcpPingService,
    sort_tcp_ping_results,
)


class _ThreadedTcpServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _NoopHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        return


@contextmanager
def _temporary_tcp_server():
    server = _ThreadedTcpServer(("127.0.0.1", 0), _NoopHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def _make_server(
    name: str,
    *,
    host: str = "127.0.0.1",
    port: int = 443,
    protocol: str = "vless",
) -> ServerEntry:
    return ServerEntry.new(
        name=name,
        protocol=protocol,
        host=host,
        port=port,
        raw_link="",
        extra={"id": f"{name}-id"},
    )


def test_tcp_ping_succeeds_against_local_tcp_server() -> None:
    service = TcpPingService()
    with _temporary_tcp_server() as port:
        server = _make_server("Local", port=port)

        result = service.ping_server(server, timeout=0.5)

    assert result.server_id == server.id
    assert result.ok is True
    assert result.latency_ms is not None
    assert result.latency_ms >= 1
    assert result.error is None


def test_tcp_ping_returns_timeout_for_socket_timeout() -> None:
    service = TcpPingService()
    server = _make_server("Timeout")

    with patch(
        "vynex_vpn_client.tcp_ping.socket.create_connection",
        side_effect=socket.timeout("timed out"),
    ):
        result = service.ping_server(server, timeout=0.01)

    assert result.ok is False
    assert result.latency_ms is None
    assert result.error == "timeout"


def test_tcp_ping_returns_refused_for_connection_refused() -> None:
    service = TcpPingService()
    server = _make_server("Refused")

    with patch(
        "vynex_vpn_client.tcp_ping.socket.create_connection",
        side_effect=ConnectionRefusedError(10061, "refused"),
    ):
        result = service.ping_server(server, timeout=0.1)

    assert result.ok is False
    assert result.latency_ms is None
    assert result.error == "refused"


def test_tcp_ping_marks_amneziawg_as_udp_only_without_connect_attempt() -> None:
    service = TcpPingService()
    server = _make_server("AWG", protocol="amneziawg")

    with patch("vynex_vpn_client.tcp_ping.socket.create_connection") as create_connection_mock:
        result = service.ping_server(server, timeout=0.1)

    assert result.ok is False
    assert result.latency_ms is None
    assert result.error == TCP_PING_UNSUPPORTED_ERROR
    create_connection_mock.assert_not_called()


def test_sort_tcp_ping_results_puts_available_servers_first_by_latency() -> None:
    fast = _make_server("Fast", host="fast.example.com", port=443)
    slow = _make_server("Slow", host="slow.example.com", port=443)
    down = _make_server("Down", host="down.example.com", port=443)
    results = [
        TcpPingResult(server_id=slow.id, ok=True, latency_ms=52, error=None, checked_at="2026-04-16T00:00:00+00:00"),
        TcpPingResult(server_id=down.id, ok=False, latency_ms=None, error="timeout", checked_at="2026-04-16T00:00:01+00:00"),
        TcpPingResult(server_id=fast.id, ok=True, latency_ms=18, error=None, checked_at="2026-04-16T00:00:02+00:00"),
    ]

    ordered = sort_tcp_ping_results([slow, down, fast], results)

    assert [server.id for server, _ in ordered] == [fast.id, slow.id, down.id]


def test_sort_tcp_ping_results_places_udp_only_before_real_failures() -> None:
    ok = _make_server("OK", host="ok.example.com", port=443)
    awg = _make_server("AWG", host="awg.example.com", port=51820, protocol="amneziawg")
    down = _make_server("Down", host="down.example.com", port=443)
    results = [
        TcpPingResult(server_id=down.id, ok=False, latency_ms=None, error="timeout", checked_at="2026-04-16T00:00:00+00:00"),
        TcpPingResult(server_id=awg.id, ok=False, latency_ms=None, error=TCP_PING_UNSUPPORTED_ERROR, checked_at="2026-04-16T00:00:01+00:00"),
        TcpPingResult(server_id=ok.id, ok=True, latency_ms=11, error=None, checked_at="2026-04-16T00:00:02+00:00"),
    ]

    ordered = sort_tcp_ping_results([down, awg, ok], results)

    assert [server.id for server, _ in ordered] == [ok.id, awg.id, down.id]


def test_tcp_ping_many_handles_empty_server_list() -> None:
    service = TcpPingService()

    assert service.ping_many([]) == []


def test_tcp_ping_marks_invalid_port_without_network_call() -> None:
    service = TcpPingService()
    server = _make_server("Broken", port=0)

    result = service.ping_many([server])[0]

    assert result.ok is False
    assert result.error == "invalid port"
