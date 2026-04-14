from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from vynex_vpn_client.process_manager import State, XrayProcessManager


def _proxy_config(*, socks_port: int = 10808, http_port: int = 18080) -> dict[str, object]:
    return {
        "inbounds": [
            {"listen": "127.0.0.1", "port": socks_port, "protocol": "socks"},
            {"listen": "127.0.0.1", "port": http_port, "protocol": "http"},
        ]
    }


def _make_proc(*, pid: int, poll_result=None, wait_result=0) -> Mock:
    proc = Mock()
    proc.pid = pid
    proc.poll.return_value = poll_result
    proc.wait.return_value = wait_result
    proc.stderr = Mock()
    proc.stderr.readline.return_value = b""
    return proc


def _make_manager(tmp_path, **kwargs) -> XrayProcessManager:
    manager = XrayProcessManager(**kwargs)
    manager._executable_path = tmp_path / "xray.exe"
    manager._executable_path.write_bytes(b"")
    return manager


def test_start_success(tmp_path) -> None:
    proc = _make_proc(pid=101, poll_result=None)

    with (
        patch("vynex_vpn_client.process_manager.atexit.register"),
        patch("vynex_vpn_client.process_manager.subprocess.Popen", return_value=proc),
        patch("vynex_vpn_client.process_manager.time.sleep", return_value=None),
        patch("vynex_vpn_client.process_manager.threading.Thread.start", return_value=None),
        patch.object(XrayProcessManager, "_check_port", return_value=True),
    ):
        manager = _make_manager(tmp_path)

        assert manager.start(_proxy_config()) == 101
        assert manager.state is State.RUNNING
        assert manager.pid == 101


def test_start_fails_immediately(tmp_path) -> None:
    proc = _make_proc(pid=102, poll_result=1)

    with (
        patch("vynex_vpn_client.process_manager.atexit.register"),
        patch("vynex_vpn_client.process_manager.subprocess.Popen", return_value=proc),
        patch("vynex_vpn_client.process_manager.time.sleep", return_value=None),
        patch("vynex_vpn_client.process_manager.threading.Thread.start", return_value=None),
        patch.object(XrayProcessManager, "_check_port", return_value=True),
    ):
        manager = _make_manager(tmp_path)

        try:
            manager.start(_proxy_config())
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected RuntimeError for immediate crash")

        assert manager.state is State.CRASHED
        assert manager.pid is None


def test_stop_releases_proc(tmp_path) -> None:
    proc = _make_proc(pid=103, poll_result=None, wait_result=0)

    with (
        patch("vynex_vpn_client.process_manager.atexit.register"),
        patch("vynex_vpn_client.process_manager.subprocess.Popen", return_value=proc),
        patch("vynex_vpn_client.process_manager.time.sleep", return_value=None),
        patch("vynex_vpn_client.process_manager.threading.Thread.start", return_value=None),
        patch.object(XrayProcessManager, "_check_port", return_value=True),
    ):
        manager = _make_manager(tmp_path)
        manager.start(_proxy_config())

        manager.stop()

        assert manager.state is State.STOPPED
        assert manager._proc is None
        proc.terminate.assert_called_once()


def test_watchdog_retries_on_crash(tmp_path) -> None:
    current_proc = _make_proc(pid=104, poll_result=None, wait_result=1)
    retry_proc_1 = _make_proc(pid=105, poll_result=1)
    retry_proc_2 = _make_proc(pid=106, poll_result=1)
    retry_proc_3 = _make_proc(pid=107, poll_result=None)

    with (
        patch("vynex_vpn_client.process_manager.atexit.register"),
        patch(
            "vynex_vpn_client.process_manager.subprocess.Popen",
            side_effect=[retry_proc_1, retry_proc_2, retry_proc_3],
        ) as popen_mock,
        patch("vynex_vpn_client.process_manager.time.sleep", return_value=None),
        patch("vynex_vpn_client.process_manager.threading.Thread.start", return_value=None),
        patch.object(XrayProcessManager, "_check_port", return_value=True),
    ):
        manager = _make_manager(tmp_path, max_retries=3, retry_delay=0.1)
        config_path = tmp_path / "xray-runtime.json"
        config_path.write_text(json.dumps(_proxy_config()), encoding="utf-8")
        manager._temp_config_path = config_path
        manager._proc = current_proc
        manager._managed_ports = (10808, 18080)
        manager._state = State.RUNNING

        def _detach_running_process() -> int:
            manager._proc = None
            return 0

        retry_proc_3.wait.side_effect = _detach_running_process

        manager._watchdog()

        assert popen_mock.call_count == 3
        assert manager.state is State.RUNNING


def test_watchdog_gives_up_after_max_retries(tmp_path) -> None:
    current_proc = _make_proc(pid=108, poll_result=None, wait_result=1)
    retry_proc_1 = _make_proc(pid=109, poll_result=1)
    retry_proc_2 = _make_proc(pid=110, poll_result=1)
    callback = Mock()

    with (
        patch("vynex_vpn_client.process_manager.atexit.register"),
        patch(
            "vynex_vpn_client.process_manager.subprocess.Popen",
            side_effect=[retry_proc_1, retry_proc_2],
        ) as popen_mock,
        patch("vynex_vpn_client.process_manager.time.sleep", return_value=None),
        patch("vynex_vpn_client.process_manager.threading.Thread.start", return_value=None),
        patch.object(XrayProcessManager, "_check_port", return_value=True),
    ):
        manager = _make_manager(tmp_path, max_retries=2, retry_delay=0.1, on_crash_callback=callback)
        config_path = tmp_path / "xray-runtime.json"
        config_path.write_text(json.dumps(_proxy_config()), encoding="utf-8")
        manager._temp_config_path = config_path
        manager._proc = current_proc
        manager._managed_ports = (10808, 18080)
        manager._state = State.RUNNING

        manager._watchdog()

        assert popen_mock.call_count == 2
        assert manager.state is State.STOPPED
        callback.assert_called_once()


def test_port_busy_by_xray_is_killed(tmp_path) -> None:
    sock = Mock()
    sock.__enter__ = Mock(return_value=sock)
    sock.__exit__ = Mock(return_value=False)
    sock.connect_ex.return_value = 0
    connection = SimpleNamespace(
        laddr=SimpleNamespace(ip="127.0.0.1", port=10808),
        pid=999,
    )
    process = Mock()
    process.name.return_value = "xray.exe"

    with (
        patch("vynex_vpn_client.process_manager.atexit.register"),
        patch("vynex_vpn_client.process_manager.socket.socket", return_value=sock),
        patch("vynex_vpn_client.process_manager.psutil.net_connections", return_value=[connection]),
        patch("vynex_vpn_client.process_manager.psutil.Process", return_value=process),
        patch("vynex_vpn_client.process_manager.time.sleep", return_value=None),
    ):
        manager = _make_manager(tmp_path)

        assert manager._check_port(10808) is True
        process.kill.assert_called_once()


def test_stderr_lines_in_queue(tmp_path) -> None:
    proc = Mock()
    proc.stderr = Mock()
    proc.stderr.readline.side_effect = [b"line 1\n", b"line 2\n", b"line 3\n", b""]

    with patch("vynex_vpn_client.process_manager.atexit.register"):
        manager = _make_manager(tmp_path)

    manager._stderr_reader(proc)

    assert list(manager.iter_logs()) == ["line 1", "line 2", "line 3"]
    assert manager.read_recent_output() == "line 1\nline 2\nline 3"
