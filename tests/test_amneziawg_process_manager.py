from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from vynex_vpn_client.amneziawg_process_manager import (
    AmneziaWgExecutableNotFoundError,
    AmneziaWgInvalidConfigError,
    AmneziaWgPermissionDeniedError,
    AmneziaWgProcessManager,
    AmneziaWgStartTimeoutError,
    AmneziaWgUnexpectedExitError,
)
from vynex_vpn_client.process_manager import State


def _completed(*, returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _runtime_config(tmp_path, *, executable_path=None, startup_timeout: float = 12.0) -> dict[str, object]:
    runtime_dir = tmp_path / "awg-runtime"
    runtime_dir.mkdir()
    config_path = runtime_dir / "office-awg.conf"
    config_path.write_text("[Interface]\nPrivateKey = secret\nAddress = 10.0.0.2/32\n", encoding="utf-8")
    return {
        "runtime_dir": str(runtime_dir),
        "config_path": str(config_path),
        "tunnel_name": "office-awg",
        "executable_path": str(executable_path) if executable_path is not None else None,
        "startup_timeout": startup_timeout,
        "stop_timeout": 0.1,
        "require_interface_ready": True,
    }


def _make_manager(tmp_path) -> tuple[AmneziaWgProcessManager, object]:
    executable_path = tmp_path / "amneziawg.exe"
    executable_path.write_bytes(b"")
    with patch("vynex_vpn_client.amneziawg_process_manager.atexit.register"):
        manager = AmneziaWgProcessManager(executable_candidates=(executable_path,))
    return manager, executable_path


def test_start_success(tmp_path) -> None:
    manager, executable_path = _make_manager(tmp_path)
    running_instance = SimpleNamespace(
        pid=401,
        executable_path=str(executable_path.resolve()).lower(),
    )

    with (
        patch("vynex_vpn_client.amneziawg_process_manager.AmneziaWgProcessManager.ensure_no_running_instances", return_value=None),
        patch("vynex_vpn_client.amneziawg_process_manager.subprocess.run", return_value=_completed()) as run_process,
        patch("vynex_vpn_client.amneziawg_process_manager.AmneziaWgProcessManager.list_running_instances", return_value=[running_instance]),
        patch("vynex_vpn_client.amneziawg_process_manager.time.sleep", return_value=None),
        patch(
            "vynex_vpn_client.amneziawg_process_manager.get_interface_details",
            return_value=SimpleNamespace(status="Up"),
        ),
    ):
        pid = manager.start(_runtime_config(tmp_path, executable_path=executable_path))

    assert pid == 401
    assert manager.pid == 401
    assert manager.state is State.RUNNING
    assert manager.status() is State.RUNNING
    assert "/installtunnelservice" in run_process.call_args.args[0]


def test_start_raises_for_missing_executable(tmp_path) -> None:
    manager, _ = _make_manager(tmp_path)
    config = _runtime_config(tmp_path, executable_path=tmp_path / "missing-awg.exe")

    try:
        manager.start(config)
    except AmneziaWgExecutableNotFoundError as exc:
        assert "не найден" in str(exc)
    else:
        raise AssertionError("Expected AmneziaWgExecutableNotFoundError for missing binary")


def test_resolve_executable_path_finds_binary_next_to_frozen_client(tmp_path) -> None:
    runtime_root = tmp_path / "runtime"
    client_dir = tmp_path / "client"
    runtime_root.mkdir()
    client_dir.mkdir()
    side_by_side_binary = client_dir / "amneziawg.exe"
    side_by_side_binary.write_bytes(b"")

    with (
        patch("vynex_vpn_client.amneziawg_process_manager.atexit.register"),
        patch("vynex_vpn_client.amneziawg_process_manager.APP_DIR", runtime_root),
        patch("vynex_vpn_client.amneziawg_process_manager.sys.executable", str(client_dir / "VynexVPNClient.exe")),
        patch("vynex_vpn_client.amneziawg_process_manager.sys.frozen", True, create=True),
    ):
        manager = AmneziaWgProcessManager(
            executable_candidates=(
                runtime_root / "amneziawg.exe",
                runtime_root / "awg.exe",
            )
        )
        resolved = manager._resolve_executable_path(None)

        assert resolved == side_by_side_binary.resolve()


def test_resolve_executable_path_finds_binary_in_local_appdata_amneziawg_dir(tmp_path) -> None:
    app_dir = tmp_path / "app"
    amneziawg_dir = tmp_path / "VynexVPNClient" / "amneziawg"
    app_dir.mkdir()
    amneziawg_dir.mkdir(parents=True)
    bundled_binary = amneziawg_dir / "awg.exe"
    bundled_binary.write_bytes(b"")

    with (
        patch("vynex_vpn_client.amneziawg_process_manager.atexit.register"),
        patch("vynex_vpn_client.amneziawg_process_manager.APP_DIR", app_dir),
        patch("vynex_vpn_client.amneziawg_process_manager.AMNEZIAWG_RUNTIME_DIR", amneziawg_dir),
        patch("vynex_vpn_client.amneziawg_process_manager.AMNEZIAWG_LEGACY_RUNTIME_DIR", tmp_path / "VynexVPNClient" / "AmneziaWG"),
        patch("vynex_vpn_client.amneziawg_process_manager.sys.frozen", False, create=True),
    ):
        manager = AmneziaWgProcessManager(
            executable_candidates=(
                tmp_path / "VynexVPNClient" / "runtime" / "amneziawg" / "amneziawg.exe",
                tmp_path / "VynexVPNClient" / "runtime" / "amneziawg" / "awg.exe",
            )
        )
        resolved = manager._resolve_executable_path(None)

        assert resolved == bundled_binary.resolve()


def test_start_raises_for_invalid_runtime_config(tmp_path) -> None:
    manager, _ = _make_manager(tmp_path)

    try:
        manager.start({"runtime_dir": str(tmp_path)})
    except AmneziaWgInvalidConfigError as exc:
        assert "config_path" in str(exc)
    else:
        raise AssertionError("Expected AmneziaWgInvalidConfigError for invalid config")


def test_start_maps_permission_error(tmp_path) -> None:
    manager, executable_path = _make_manager(tmp_path)

    with patch(
        "vynex_vpn_client.amneziawg_process_manager.AmneziaWgProcessManager.ensure_no_running_instances",
        return_value=None,
    ), patch(
        "vynex_vpn_client.amneziawg_process_manager.subprocess.run",
        side_effect=PermissionError("access denied"),
    ):
        try:
            manager.start(_runtime_config(tmp_path, executable_path=executable_path))
        except AmneziaWgPermissionDeniedError as exc:
            assert "доступ запрещен" in str(exc).lower()
        else:
            raise AssertionError("Expected AmneziaWgPermissionDeniedError for denied spawn")


def test_start_times_out_when_interface_not_ready(tmp_path) -> None:
    manager, executable_path = _make_manager(tmp_path)
    running_instance = SimpleNamespace(
        pid=402,
        executable_path=str(executable_path.resolve()).lower(),
    )

    with (
        patch("vynex_vpn_client.amneziawg_process_manager.AmneziaWgProcessManager.ensure_no_running_instances", return_value=None),
        patch("vynex_vpn_client.amneziawg_process_manager.subprocess.run", return_value=_completed()),
        patch("vynex_vpn_client.amneziawg_process_manager.AmneziaWgProcessManager.list_running_instances", return_value=[running_instance]),
        patch("vynex_vpn_client.amneziawg_process_manager.AmneziaWgProcessManager._cleanup_tunnel_service", return_value=None),
        patch("vynex_vpn_client.amneziawg_process_manager.is_process_running", return_value=False),
        patch("vynex_vpn_client.amneziawg_process_manager.time.sleep", return_value=None),
        patch("vynex_vpn_client.amneziawg_process_manager.AmneziaWgProcessManager._wait_for_threads", return_value=None),
        patch("vynex_vpn_client.amneziawg_process_manager.get_interface_details", return_value=None),
        patch("vynex_vpn_client.amneziawg_process_manager.time.monotonic", side_effect=[0.0, 0.0, 0.2]),
    ):
        try:
            manager.start(_runtime_config(tmp_path, executable_path=executable_path, startup_timeout=0.1))
        except AmneziaWgStartTimeoutError as exc:
            assert "превышено время ожидания" in str(exc).lower()
        else:
            raise AssertionError("Expected AmneziaWgStartTimeoutError when AWG interface is not ready")

def test_start_raises_unexpected_exit_when_install_helper_fails(tmp_path) -> None:
    manager, executable_path = _make_manager(tmp_path)

    with (
        patch("vynex_vpn_client.amneziawg_process_manager.AmneziaWgProcessManager.ensure_no_running_instances", return_value=None),
        patch(
            "vynex_vpn_client.amneziawg_process_manager.subprocess.run",
            return_value=_completed(returncode=1, stderr="The service process could not connect to the service controller."),
        ),
        patch("vynex_vpn_client.amneziawg_process_manager.AmneziaWgProcessManager._cleanup_tunnel_service", return_value=None),
    ):
        try:
            manager.start(_runtime_config(tmp_path, executable_path=executable_path))
        except AmneziaWgUnexpectedExitError as exc:
            assert "неожиданно завершился" in str(exc).lower()
            assert "service controller" in str(exc).lower()
        else:
            raise AssertionError("Expected AmneziaWgUnexpectedExitError for failed service install helper")


def test_collect_output_returns_stream_tails(tmp_path) -> None:
    manager, _ = _make_manager(tmp_path)

    with manager._state_lock:
        manager._stdout_tail.extend(["out-1", "out-2"])
        manager._stderr_tail.extend(["err-1"])
        manager._output_tail.extend(["[stdout] out-1", "[stdout] out-2", "[stderr] err-1"])

    snapshot = manager.collect_output()

    assert snapshot.stdout == ("out-1", "out-2")
    assert snapshot.stderr == ("err-1",)
    assert "err-1" in manager.read_recent_output()


def test_cleanup_runtime_artifacts_requests_tunnel_uninstall(tmp_path) -> None:
    manager, executable_path = _make_manager(tmp_path)
    runtime_dir = tmp_path / "awg-runtime-cleanup"
    runtime_dir.mkdir()

    manager._runtime_dir = runtime_dir
    manager._executable_path = executable_path
    manager._tunnel_name = "office-awg"

    with patch(
        "vynex_vpn_client.amneziawg_process_manager.subprocess.run",
        return_value=_completed(),
    ) as run_process:
        manager._cleanup_runtime_artifacts()

    run_process.assert_called_once()
    assert "/uninstalltunnelservice" in run_process.call_args.args[0]
    assert not runtime_dir.exists()


def test_cleanup_tunnel_service_downgrades_permission_denied_to_warning(tmp_path) -> None:
    manager, executable_path = _make_manager(tmp_path)

    with (
        patch.object(manager._logger, "warning") as warning_log,
        patch.object(manager._logger, "exception") as exception_log,
        patch(
            "vynex_vpn_client.amneziawg_process_manager.subprocess.run",
            side_effect=PermissionError("access denied"),
        ),
    ):
        manager._cleanup_tunnel_service(executable_path=executable_path, tunnel_name="office-awg")

    warning_log.assert_called_once()
    exception_log.assert_not_called()
