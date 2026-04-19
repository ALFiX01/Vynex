from __future__ import annotations

import atexit
import csv
from collections import deque
from dataclasses import dataclass
import logging
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import psutil

from .constants import (
    AMNEZIAWG_LEGACY_RUNTIME_DIR,
    AMNEZIAWG_RUNTIME_DIR,
    APP_DIR,
    AMNEZIAWG_EXECUTABLE,
    AMNEZIAWG_EXECUTABLE_FALLBACK,
    AMNEZIAWG_PROCESS_LOG,
)
from .process_manager import ProcessInstanceInfo, State, _build_process_logger
from .utils import get_interface_details, is_process_running


class AmneziaWgProcessError(RuntimeError):
    pass


class AmneziaWgExecutableNotFoundError(AmneziaWgProcessError, FileNotFoundError):
    pass


class AmneziaWgInvalidConfigError(AmneziaWgProcessError, ValueError):
    pass


class AmneziaWgPermissionDeniedError(AmneziaWgProcessError, PermissionError):
    pass


class AmneziaWgStartTimeoutError(AmneziaWgProcessError, TimeoutError):
    pass


class AmneziaWgUnexpectedExitError(AmneziaWgProcessError):
    pass


@dataclass(frozen=True)
class AmneziaWgOutputSnapshot:
    stdout: tuple[str, ...]
    stderr: tuple[str, ...]


@dataclass(frozen=True)
class _AmneziaWgLaunchConfig:
    executable_path: Path
    config_path: Path
    runtime_dir: Path
    tunnel_name: str
    startup_timeout: float
    stop_timeout: float
    require_interface_ready: bool


class AmneziaWgProcessManager:
    PROCESS_IMAGE_NAME = "amneziawg.exe"
    FALLBACK_IMAGE_NAME = "awg.exe"
    OUTPUT_TAIL_LIMIT = 120
    STARTUP_TIMEOUT_SECONDS = 12.0
    STOP_TIMEOUT_SECONDS = 5.0
    STARTUP_POLL_INTERVAL = 0.2

    def __init__(
        self,
        *,
        executable_candidates: tuple[Path, ...] | None = None,
        on_crash_callback: Callable[[], None] | None = None,
    ) -> None:
        self._executable_candidates = executable_candidates or (
            AMNEZIAWG_EXECUTABLE,
            AMNEZIAWG_EXECUTABLE_FALLBACK,
        )
        self._process: subprocess.Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._watcher_thread: threading.Thread | None = None
        self._output_tail: deque[str] = deque(maxlen=self.OUTPUT_TAIL_LIMIT)
        self._stdout_tail: deque[str] = deque(maxlen=self.OUTPUT_TAIL_LIMIT)
        self._stderr_tail: deque[str] = deque(maxlen=self.OUTPUT_TAIL_LIMIT)
        self._state = State.STOPPED
        self._state_lock = threading.RLock()
        self._stopping_pids: set[int] = set()
        self._runtime_dir: Path | None = None
        self._config_path: Path | None = None
        self._executable_path: Path | None = None
        self._tunnel_name: str | None = None
        self._active_pid: int | None = None
        self.on_crash_callback = on_crash_callback
        self._logger = _build_process_logger(
            "vynex_vpn_client.process.amneziawg",
            AMNEZIAWG_PROCESS_LOG,
        )
        atexit.register(self.stop)

    @property
    def pid(self) -> int | None:
        with self._state_lock:
            if self._process is not None:
                return self._process.pid
            return self._active_pid

    @property
    def state(self) -> State:
        with self._state_lock:
            return self._state

    def status(self) -> State:
        return self.state

    def collect_output(self, limit: int = 50) -> AmneziaWgOutputSnapshot:
        with self._state_lock:
            return AmneziaWgOutputSnapshot(
                stdout=tuple(list(self._stdout_tail)[-limit:]),
                stderr=tuple(list(self._stderr_tail)[-limit:]),
            )

    def read_recent_output(self, limit: int = 15) -> str:
        with self._state_lock:
            tail = list(self._output_tail)[-limit:]
        return "\n".join(tail) or "AmneziaWG завершился без вывода в лог."

    def start(self, config: dict[str, Any]) -> int:
        try:
            launch_config = self._parse_launch_config(config)
        except AmneziaWgProcessError:
            self._cleanup_runtime_dir_from_config(config)
            raise
        with self._state_lock:
            if self._state in {State.STARTING, State.RUNNING, State.STOPPING} and self.pid is not None:
                raise RuntimeError("AmneziaWG уже запущен этим клиентом.")

        self.ensure_no_running_instances()
        self._finalize_process(cleanup_runtime=True)
        self._clear_output_tail()
        with self._state_lock:
            self._state = State.STARTING
            self._runtime_dir = launch_config.runtime_dir
            self._config_path = launch_config.config_path
            self._executable_path = launch_config.executable_path
            self._tunnel_name = launch_config.tunnel_name
            self._active_pid = None

        command = [
            str(launch_config.executable_path),
            "/installtunnelservice",
            str(launch_config.config_path),
        ]
        self._logger.info(
            "Starting AmneziaWG with config_path=%s tunnel=%s",
            launch_config.config_path,
            launch_config.tunnel_name,
        )
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                command,
                cwd=str(launch_config.runtime_dir),
                creationflags=creationflags,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=max(launch_config.startup_timeout, 5.0),
            )
        except FileNotFoundError as exc:
            self._set_state(State.STOPPED)
            self._cleanup_runtime_artifacts()
            raise AmneziaWgExecutableNotFoundError(
                f"AmneziaWG executable не найден: {launch_config.executable_path}"
            ) from exc
        except PermissionError as exc:
            self._set_state(State.STOPPED)
            self._cleanup_runtime_artifacts()
            raise AmneziaWgPermissionDeniedError(
                f"Доступ запрещен при запуске AmneziaWG backend: {launch_config.executable_path}"
            ) from exc
        except OSError as exc:
            self._set_state(State.STOPPED)
            self._cleanup_runtime_artifacts()
            if self._is_permission_error(exc):
                raise AmneziaWgPermissionDeniedError(
                    f"Доступ запрещен при запуске AmneziaWG backend: {launch_config.executable_path}"
                ) from exc
            raise AmneziaWgProcessError(
                f"Не удалось запустить AmneziaWG backend. Подробности в логе: {AMNEZIAWG_PROCESS_LOG}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            self._set_state(State.STOPPED)
            self._record_process_output(stdout=exc.stdout, stderr=exc.stderr)
            self._cleanup_runtime_artifacts()
            raise AmneziaWgProcessError(
                "AmneziaWG helper установки туннельного сервиса не завершился вовремя."
            ) from exc

        self._record_process_output(stdout=result.stdout, stderr=result.stderr)
        if result.returncode != 0:
            error_message = self.read_recent_output()
            self._logger.error(
                "AmneziaWG install helper exited with code %s",
                result.returncode,
            )
            self._set_state(State.STOPPED)
            self._cleanup_runtime_artifacts()
            raise AmneziaWgUnexpectedExitError(
                "AmneziaWG backend неожиданно завершился во время запуска.\n"
                f"{error_message}"
            )

        deadline = time.monotonic() + launch_config.startup_timeout
        poll_interval = self.STARTUP_POLL_INTERVAL
        while time.monotonic() < deadline:
            running_pid = self._discover_service_pid()
            if running_pid is not None and self._healthcheck_ready(launch_config):
                with self._state_lock:
                    self._active_pid = running_pid
                self._set_state(State.RUNNING)
                return running_pid
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(poll_interval, remaining))
            poll_interval = min(poll_interval * 1.5, 0.75)

        self._logger.error(
            "AmneziaWG tunnel '%s' did not pass startup health-check within %.1fs",
            launch_config.tunnel_name,
            launch_config.startup_timeout,
        )
        self._finalize_process(cleanup_runtime=True)
        raise AmneziaWgStartTimeoutError(
            "Превышено время ожидания запуска AmneziaWG backend. "
            f"Интерфейс '{launch_config.tunnel_name}' не перешел в состояние ready."
        )

    def stop(self, pid: int | None = None) -> None:
        target_pid = pid or self.pid
        if not target_pid:
            self._set_state(State.STOPPED)
            self._cleanup_runtime_artifacts()
            return

        self._set_state(State.STOPPING)
        process = self._managed_process_for_pid(target_pid)
        timeout = self.STOP_TIMEOUT_SECONDS
        if process is not None:
            self._stop_managed_process(process, timeout=timeout)
            self._finalize_process(cleanup_runtime=True)
            self._set_state(State.STOPPED)
            return
        if is_process_running(target_pid) and self._is_target_pid(target_pid):
            self._stop_external_process(target_pid, timeout=timeout)
        self._finalize_process(cleanup_runtime=True)
        self._set_state(State.STOPPED)

    def restart(self, config: dict[str, Any]) -> int:
        self.stop(self.pid)
        return self.start(config)

    def is_running(self, pid: int | None) -> bool:
        with self._state_lock:
            process = self._process
            if process is not None and process.poll() is None:
                return True
        return bool(pid and is_process_running(pid) and self._is_target_pid(pid))

    def ensure_no_running_instances(self, *, exclude_pid: int | None = None) -> None:
        current_pid = self.pid
        if exclude_pid is None:
            exclude_pid = current_pid
        running_instances = [
            instance
            for instance in self.list_running_instances()
            if exclude_pid is None or instance.pid != exclude_pid
        ]
        if not running_instances:
            return
        raise RuntimeError(self._format_running_instances_error(running_instances))

    def list_running_instances(self) -> list[ProcessInstanceInfo]:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process | "
                    f"Where-Object {{ $_.Name -in @('{self.PROCESS_IMAGE_NAME}', '{self.FALLBACK_IMAGE_NAME}') }} | "
                    "Select-Object ProcessId, Name, ExecutablePath | ConvertTo-Csv -NoTypeInformation"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=creationflags,
        )
        if result.returncode != 0:
            self._logger.warning(
                "Failed to enumerate AmneziaWG processes, return code %s",
                result.returncode,
            )
            return []
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if len(lines) <= 1:
            return []
        instances: list[ProcessInstanceInfo] = []
        for row in csv.DictReader(lines):
            raw_pid = (row.get("ProcessId") or "").strip()
            if not raw_pid.isdigit():
                continue
            raw_path = (row.get("ExecutablePath") or "").strip() or None
            instances.append(
                ProcessInstanceInfo(
                    pid=int(raw_pid),
                    executable_path=self._normalize_path(raw_path),
                )
            )
        return instances

    def _parse_launch_config(self, config: dict[str, Any]) -> _AmneziaWgLaunchConfig:
        if not isinstance(config, dict):
            raise AmneziaWgInvalidConfigError("Невалидный runtime config AmneziaWG: ожидается словарь.")
        config_path_value = config.get("config_path")
        if not config_path_value:
            raise AmneziaWgInvalidConfigError(
                "Невалидный runtime config AmneziaWG: отсутствует config_path."
            )
        config_path = Path(str(config_path_value)).expanduser()
        if not config_path.exists() or not config_path.is_file():
            raise AmneziaWgInvalidConfigError(
                f"Невалидный runtime config AmneziaWG: файл конфигурации не найден: {config_path}"
            )
        runtime_dir_value = config.get("runtime_dir")
        runtime_dir = Path(str(runtime_dir_value)).expanduser() if runtime_dir_value else config_path.parent
        if not runtime_dir.exists() or not runtime_dir.is_dir():
            raise AmneziaWgInvalidConfigError(
                f"Невалидный runtime config AmneziaWG: runtime_dir не существует: {runtime_dir}"
            )
        tunnel_name = str(config.get("tunnel_name") or config_path.stem).strip()
        if not tunnel_name:
            raise AmneziaWgInvalidConfigError(
                "Невалидный runtime config AmneziaWG: отсутствует tunnel_name."
            )
        executable_path = self._resolve_executable_path(config.get("executable_path"))
        startup_timeout = self._coerce_positive_float(
            config.get("startup_timeout", self.STARTUP_TIMEOUT_SECONDS),
            field_name="startup_timeout",
        )
        stop_timeout = self._coerce_positive_float(
            config.get("stop_timeout", self.STOP_TIMEOUT_SECONDS),
            field_name="stop_timeout",
        )
        require_interface_ready = self._coerce_bool(
            config.get("require_interface_ready", True),
            field_name="require_interface_ready",
        )
        return _AmneziaWgLaunchConfig(
            executable_path=executable_path,
            config_path=config_path,
            runtime_dir=runtime_dir,
            tunnel_name=tunnel_name,
            startup_timeout=startup_timeout,
            stop_timeout=stop_timeout,
            require_interface_ready=require_interface_ready,
        )

    def _resolve_executable_path(self, explicit_path: object) -> Path:
        if explicit_path is not None:
            candidate = self._normalize_candidate_path(Path(str(explicit_path)).expanduser())
            if candidate.exists():
                return candidate
            raise AmneziaWgExecutableNotFoundError(
                f"AmneziaWG executable не найден: {candidate}"
            )
        searched_paths: list[Path] = []
        for candidate in self._iter_executable_candidates():
            normalized = self._normalize_candidate_path(candidate)
            searched_paths.append(normalized)
            if normalized.exists():
                return normalized
        searched_locations = ", ".join(str(path) for path in searched_paths[:6])
        if len(searched_paths) > 6:
            searched_locations += ", ..."
        raise AmneziaWgExecutableNotFoundError(
            "AmneziaWG executable не найден. "
            f"Проверьте наличие {AMNEZIAWG_EXECUTABLE.name} или {AMNEZIAWG_EXECUTABLE_FALLBACK.name}. "
            f"Пути поиска: {searched_locations}"
        )

    def _iter_executable_candidates(self) -> tuple[Path, ...]:
        candidates: list[Path] = []
        seen: set[str] = set()
        for candidate in (*self._executable_candidates, *self._side_by_side_candidates()):
            normalized = self._normalize_candidate_path(candidate)
            key = self._normalize_path(str(normalized))
            if key is None or key in seen:
                continue
            seen.add(key)
            candidates.append(normalized)
        return tuple(candidates)

    def _side_by_side_candidates(self) -> tuple[Path, ...]:
        directories: list[Path] = []
        directories.extend(self._side_by_side_search_dirs())
        executable_names = self._candidate_file_names()
        return tuple(directory / executable_name for directory in directories for executable_name in executable_names)

    def _side_by_side_search_dirs(self) -> tuple[Path, ...]:
        directories: list[Path] = []
        seen: set[str] = set()
        for raw_directory in self._raw_side_by_side_search_dirs():
            directory = self._normalize_candidate_path(raw_directory)
            key = self._normalize_path(str(directory))
            if key is None or key in seen:
                continue
            seen.add(key)
            directories.append(directory)
        return tuple(directories)

    def _raw_side_by_side_search_dirs(self) -> tuple[Path, ...]:
        directories: list[Path] = [APP_DIR]
        if getattr(sys, "frozen", False):
            try:
                directories.append(Path(sys.executable).resolve().parent)
            except OSError:
                directories.append(Path(sys.executable).parent)
        directories.extend((AMNEZIAWG_RUNTIME_DIR, AMNEZIAWG_LEGACY_RUNTIME_DIR))
        return tuple(directories)

    def _candidate_file_names(self) -> tuple[str, ...]:
        file_names = [
            AMNEZIAWG_EXECUTABLE.name,
            AMNEZIAWG_EXECUTABLE_FALLBACK.name,
        ]
        for candidate in self._executable_candidates:
            name = Path(candidate).name
            if name and name not in file_names:
                file_names.append(name)
        return tuple(file_names)

    @staticmethod
    def _normalize_candidate_path(path: Path) -> Path:
        try:
            return path.resolve(strict=False)
        except OSError:
            return path

    def _healthcheck_ready(self, config: _AmneziaWgLaunchConfig) -> bool:
        if not config.require_interface_ready:
            return True
        # TODO: Replace interface-only readiness with a backend-native health probe once
        # amneziawg-windows exposes a stable CLI/API for tunnel handshake or peer state.
        details = get_interface_details(config.tunnel_name, allow_link_local=True)
        if details is None:
            return False
        return str(details.status or "").lower() == "up"

    def _discover_service_pid(self) -> int | None:
        fast_pid = self._discover_service_pid_fast()
        if fast_pid is not None:
            return fast_pid
        target_paths = {self._normalize_path(str(path)) for path in self._iter_executable_candidates()}
        for instance in self.list_running_instances():
            if instance.executable_path in target_paths:
                return instance.pid
        return None

    def _discover_service_pid_fast(self) -> int | None:
        target_names = {name.lower() for name in self._candidate_file_names()}
        target_paths = {self._normalize_path(str(path)) for path in self._iter_executable_candidates()}
        try:
            for process in psutil.process_iter(["pid", "name", "exe"]):
                try:
                    process_name = str(process.info.get("name") or "").strip().lower()
                    if not process_name or process_name not in target_names:
                        continue
                    executable_path = self._normalize_path(str(process.info.get("exe") or "").strip() or None)
                    if executable_path in target_paths:
                        return int(process.info["pid"])
                except (KeyError, TypeError, ValueError, psutil.Error, OSError):
                    continue
        except (psutil.AccessDenied, OSError):
            return None
        return None

    def _register_process(self, process: subprocess.Popen[str]) -> None:
        stdout_thread = threading.Thread(
            target=self._capture_stream,
            args=(process, "stdout"),
            name="amneziawg-stdout-reader",
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=self._capture_stream,
            args=(process, "stderr"),
            name="amneziawg-stderr-reader",
            daemon=True,
        )
        watcher_thread = threading.Thread(
            target=self._watch_process,
            args=(process,),
            name="amneziawg-watchdog",
            daemon=True,
        )
        with self._state_lock:
            self._process = process
            self._stdout_thread = stdout_thread
            self._stderr_thread = stderr_thread
            self._watcher_thread = watcher_thread
        stdout_thread.start()
        stderr_thread.start()
        watcher_thread.start()

    def _capture_stream(self, process: subprocess.Popen[str], stream_name: str) -> None:
        stream = getattr(process, stream_name)
        if stream is None:
            return
        try:
            for raw_line in stream:
                line = raw_line.rstrip()
                if not line:
                    continue
                self._append_output_line(stream_name, line)
        except Exception:  # noqa: BLE001
            self._logger.exception("Failed to read AmneziaWG %s", stream_name)

    def _record_process_output(self, *, stdout: str | None, stderr: str | None) -> None:
        for line in str(stdout or "").splitlines():
            normalized = line.rstrip()
            if normalized:
                self._append_output_line("stdout", normalized)
        for line in str(stderr or "").splitlines():
            normalized = line.rstrip()
            if normalized:
                self._append_output_line("stderr", normalized)

    def _append_output_line(self, stream_name: str, line: str) -> None:
        prefixed = f"[{stream_name}] {line}"
        with self._state_lock:
            self._output_tail.append(prefixed)
            if stream_name == "stdout":
                self._stdout_tail.append(line)
            else:
                self._stderr_tail.append(line)
        self._logger.info("[amneziawg:%s] %s", stream_name, line)

    def _watch_process(self, process: subprocess.Popen[str]) -> None:
        try:
            return_code = process.wait()
        except Exception:  # noqa: BLE001
            self._logger.exception("Failed to wait for AmneziaWG pid=%s", process.pid)
            return

        callback: Callable[[], None] | None = None
        with self._state_lock:
            intentional_stop = process.pid in self._stopping_pids
            self._stopping_pids.discard(process.pid)
            if self._process is process:
                self._process = None
            if intentional_stop:
                self._state = State.STOPPED
            else:
                self._state = State.CRASHED
                callback = self.on_crash_callback
        self._close_streams(process)
        if intentional_stop:
            self._logger.info("AmneziaWG pid=%s stopped with code %s", process.pid, return_code)
            self._cleanup_runtime_artifacts()
            return

        self._logger.warning(
            "AmneziaWG pid=%s exited unexpectedly with code %s",
            process.pid,
            return_code,
        )
        self._cleanup_runtime_artifacts()
        if callback is not None:
            try:
                callback()
            except Exception:  # noqa: BLE001
                self._logger.exception("AmneziaWG crash callback failed")

    def _managed_process_for_pid(self, pid: int) -> subprocess.Popen[str] | None:
        with self._state_lock:
            if self._process is not None and self._process.pid == pid:
                return self._process
        return None

    def _stop_managed_process(self, process: subprocess.Popen[str], *, timeout: float) -> None:
        with self._state_lock:
            self._stopping_pids.add(process.pid)
        if process.poll() is not None:
            return
        self._logger.info("Stopping AmneziaWG pid=%s", process.pid)
        try:
            process.terminate()
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._logger.warning(
                "AmneziaWG pid=%s did not stop within %.1fs, killing",
                process.pid,
                timeout,
            )
            process.kill()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._logger.error(
                    "AmneziaWG pid=%s did not exit after kill within %.1fs",
                    process.pid,
                    timeout,
                )
        except OSError:
            self._logger.exception("Failed to terminate AmneziaWG pid=%s", process.pid)
        finally:
            self._close_streams(process)

    def _stop_external_process(self, pid: int, *, timeout: float) -> None:
        self._logger.info("Stopping external AmneziaWG pid=%s", pid)
        self._run_taskkill(pid, force=False)
        if self._wait_for_pid_exit(pid, timeout=timeout):
            return
        self._logger.warning(
            "External AmneziaWG pid=%s did not stop within %.1fs, forcing termination",
            pid,
            timeout,
        )
        self._run_taskkill(pid, force=True)
        if not self._wait_for_pid_exit(pid, timeout=timeout):
            self._logger.error("External AmneziaWG pid=%s is still running after forced termination", pid)

    def _run_taskkill(self, pid: int, *, force: bool) -> None:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        command = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            command.append("/F")
        subprocess.run(
            command,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )

    def _wait_for_pid_exit(self, pid: int, *, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not is_process_running(pid) or not self._is_target_pid(pid):
                return True
            time.sleep(0.2)
        return not is_process_running(pid) or not self._is_target_pid(pid)

    def _is_target_pid(self, pid: int) -> bool:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            creationflags=creationflags,
        )
        line = result.stdout.strip()
        if not line or line.startswith("INFO:"):
            return False
        try:
            row = next(csv.reader([line]))
        except StopIteration:
            return False
        if not row:
            return False
        image_name = row[0].lower()
        return image_name in {self.PROCESS_IMAGE_NAME.lower(), self.FALLBACK_IMAGE_NAME.lower()}

    def _clear_output_tail(self) -> None:
        with self._state_lock:
            self._output_tail.clear()
            self._stdout_tail.clear()
            self._stderr_tail.clear()

    def _wait_for_threads(self, *, timeout: float) -> None:
        current_thread = threading.current_thread()
        with self._state_lock:
            threads = [
                thread
                for thread in (self._stdout_thread, self._stderr_thread, self._watcher_thread)
                if thread is not None
            ]
        for thread in threads:
            if thread is current_thread:
                continue
            thread.join(timeout=timeout)

    def _finalize_process(self, *, cleanup_runtime: bool) -> None:
        self._wait_for_threads(timeout=0.5)
        with self._state_lock:
            process = self._process
            self._process = None
            self._stdout_thread = None
            self._stderr_thread = None
            self._watcher_thread = None
        if process is not None:
            self._close_streams(process)
        if cleanup_runtime:
            self._cleanup_runtime_artifacts()

    def _cleanup_runtime_artifacts(self) -> None:
        with self._state_lock:
            runtime_dir = self._runtime_dir
            self._runtime_dir = None
            self._config_path = None
            executable_path = self._executable_path
            self._executable_path = None
            tunnel_name = self._tunnel_name
            self._tunnel_name = None
            active_pid = self._active_pid
            self._active_pid = None
        self._cleanup_tunnel_service(executable_path=executable_path, tunnel_name=tunnel_name)
        if active_pid and is_process_running(active_pid) and self._is_target_pid(active_pid):
            if not self._wait_for_pid_exit(active_pid, timeout=self.STOP_TIMEOUT_SECONDS):
                self._stop_external_process(active_pid, timeout=self.STOP_TIMEOUT_SECONDS)
        if runtime_dir is None:
            return
        try:
            shutil.rmtree(runtime_dir, ignore_errors=True)
        except OSError:
            self._logger.exception("Failed to cleanup AmneziaWG runtime dir %s", runtime_dir)

    def _cleanup_runtime_dir_from_config(self, config: object) -> None:
        if not isinstance(config, dict):
            return
        runtime_dir_value = config.get("runtime_dir")
        config_path_value = config.get("config_path")
        if not runtime_dir_value or not config_path_value:
            return
        runtime_dir = Path(str(runtime_dir_value)).expanduser()
        config_path = Path(str(config_path_value)).expanduser()
        try:
            resolved_runtime_dir = runtime_dir.resolve()
            resolved_config_path = config_path.resolve()
        except OSError:
            return
        if not resolved_runtime_dir.exists() or not resolved_runtime_dir.is_dir():
            return
        if resolved_config_path.parent != resolved_runtime_dir:
            return
        try:
            shutil.rmtree(resolved_runtime_dir, ignore_errors=True)
        except OSError:
            self._logger.exception("Failed to cleanup AmneziaWG runtime dir %s after early startup failure", runtime_dir)

    def _cleanup_tunnel_service(self, *, executable_path: Path | None, tunnel_name: str | None) -> None:
        if executable_path is None or tunnel_name is None:
            return
        if not executable_path.exists():
            return
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(
                [str(executable_path), "/uninstalltunnelservice", tunnel_name],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=creationflags,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            self._logger.warning("Timed out while cleaning up AmneziaWG tunnel service '%s'", tunnel_name)
            return
        except OSError:
            self._logger.exception("Failed to invoke AmneziaWG cleanup for tunnel '%s'", tunnel_name)
            return
        if result.returncode == 0:
            self._logger.info("Requested AmneziaWG tunnel cleanup for '%s'", tunnel_name)
            return
        output = (result.stderr or result.stdout).strip().lower()
        if not output or "not found" in output or "не найден" in output:
            return
        self._logger.warning(
            "Failed to cleanup AmneziaWG tunnel service '%s': %s",
            tunnel_name,
            (result.stderr.strip() or result.stdout.strip() or f"exit={result.returncode}"),
        )

    def _set_state(self, state: State) -> None:
        with self._state_lock:
            self._state = state

    def _format_running_instances_error(self, instances: list[ProcessInstanceInfo]) -> str:
        lines = [
            "Уже запущен AmneziaWG backend. Сначала остановите существующий экземпляр."
        ]
        target_paths = {self._normalize_path(str(path)) for path in self._iter_executable_candidates()}
        for instance in instances[:5]:
            location = instance.executable_path or "путь не определен"
            source = "управляемый клиентом" if instance.executable_path in target_paths else "внешний"
            lines.append(f"PID {instance.pid} [{source}] - {location}")
        if len(instances) > 5:
            lines.append(f"И еще экземпляров: {len(instances) - 5}")
        return "\n".join(lines)

    @staticmethod
    def _normalize_path(raw_path: str | None) -> str | None:
        if not raw_path:
            return None
        try:
            return str(Path(raw_path).resolve()).lower()
        except OSError:
            return str(Path(raw_path)).lower()

    @staticmethod
    def _close_streams(process: subprocess.Popen[str]) -> None:
        for stream_name in ("stdout", "stderr"):
            stream = getattr(process, stream_name, None)
            if stream is None:
                continue
            try:
                stream.close()
            except OSError:
                pass

    @staticmethod
    def _coerce_positive_float(value: object, *, field_name: str) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise AmneziaWgInvalidConfigError(
                f"Невалидный runtime config AmneziaWG: {field_name} должен быть числом."
            ) from exc
        if numeric <= 0:
            raise AmneziaWgInvalidConfigError(
                f"Невалидный runtime config AmneziaWG: {field_name} должен быть больше нуля."
            )
        return numeric

    @staticmethod
    def _coerce_bool(value: object, *, field_name: str) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        raise AmneziaWgInvalidConfigError(
            f"Невалидный runtime config AmneziaWG: {field_name} должен быть bool."
        )

    @staticmethod
    def _is_permission_error(exc: OSError) -> bool:
        return isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 5
