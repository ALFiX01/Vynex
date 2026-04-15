from __future__ import annotations

import atexit
import csv
from collections import deque
from dataclasses import dataclass
from enum import Enum
import json
import logging
import queue
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Generator

import psutil

from .constants import (
    SINGBOX_EXECUTABLE,
    SINGBOX_PROCESS_LOG,
    XRAY_EXECUTABLE,
    XRAY_PROCESS_LOG,
    XRAY_RUNTIME_DIR,
)
from .utils import is_process_running


@dataclass(frozen=True)
class ProcessInstanceInfo:
    pid: int
    executable_path: str | None = None


class State(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    CRASHED = "crashed"


def _build_process_logger(name: str, path: Path) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    resolved_path = str(path.resolve())
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler) and handler.baseFilename == resolved_path:
            return logger
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
    except OSError:
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())
        return logger
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


class _BaseProcessManager:
    STARTUP_GRACE_PERIOD = 1.5
    STOP_TIMEOUT_SECONDS = 5.0
    OUTPUT_TAIL_LIMIT = 120

    def __init__(
        self,
        *,
        process_image_name: str,
        executable_path: Path,
        log_path: Path,
        temp_config_prefix: str,
        missing_executable_message: str,
        immediate_exit_message: str,
    ) -> None:
        self._process_image_name = process_image_name
        self._executable_path = executable_path
        self._log_path = log_path
        self._temp_config_prefix = temp_config_prefix
        self._missing_executable_message = missing_executable_message
        self._immediate_exit_message = immediate_exit_message
        self._process: subprocess.Popen[str] | None = None
        self._output_tail: deque[str] = deque(maxlen=self.OUTPUT_TAIL_LIMIT)
        self._output_thread: threading.Thread | None = None
        self._watcher_thread: threading.Thread | None = None
        self._temp_config_path: Path | None = None
        self._state_lock = threading.RLock()
        self._stopping_pids: set[int] = set()
        self._logger = _build_process_logger(
            f"vynex_vpn_client.process.{self._process_image_name.lower()}",
            self._log_path,
        )

    @property
    def pid(self) -> int | None:
        with self._state_lock:
            if self._process is None:
                return None
            return self._process.pid

    def start(self, config: dict[str, Any]) -> int:
        if not self._executable_path.exists():
            raise FileNotFoundError(self._missing_executable_message)
        self.ensure_no_running_instances()
        self._finalize_process()
        config_path = self._write_temp_config(config)
        self._clear_output_tail()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._logger.info("Starting %s with config %s", self._process_image_name, config_path)
        try:
            process = subprocess.Popen(
                [str(self._executable_path), "run", "-c", str(config_path)],
                cwd=str(XRAY_RUNTIME_DIR),
                creationflags=creationflags,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Failed to start %s", self._process_image_name)
            self._cleanup_temp_config()
            raise RuntimeError(
                f"Не удалось запустить {self._process_image_name}. "
                f"Подробности в логе: {self._log_path}"
            ) from exc
        self._register_process(process)
        try:
            time.sleep(self.STARTUP_GRACE_PERIOD)
            if process.poll() is not None:
                self._wait_for_threads(timeout=0.5)
                error_message = self._read_last_log_lines()
                self._logger.error(
                    "%s exited immediately with code %s",
                    self._process_image_name,
                    process.returncode,
                )
                self._finalize_process()
                raise RuntimeError(error_message)
            return int(process.pid)
        finally:
            self._cleanup_temp_config()

    def stop(self, pid: int | None) -> None:
        if not pid:
            return
        process = self._managed_process_for_pid(pid)
        if process is not None:
            self._stop_managed_process(process, timeout=self.STOP_TIMEOUT_SECONDS)
            self._finalize_process()
            return
        if not is_process_running(pid) or not self._is_target_pid(pid):
            self._finalize_process()
            return
        self._stop_external_process(pid, timeout=self.STOP_TIMEOUT_SECONDS)
        self._finalize_process()

    def is_running(self, pid: int | None) -> bool:
        return bool(pid and is_process_running(pid) and self._is_target_pid(pid))

    def ensure_no_running_instances(self, *, exclude_pid: int | None = None) -> None:
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
                f"Get-CimInstance Win32_Process -Filter \"Name='{self._process_image_name}'\" | "
                "Select-Object ProcessId, ExecutablePath | ConvertTo-Csv -NoTypeInformation",
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
                "Failed to enumerate %s processes, return code %s",
                self._process_image_name,
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

    def read_recent_output(self, limit: int = 15) -> str:
        return self._read_last_log_lines(limit)

    def _clear_output_tail(self) -> None:
        with self._state_lock:
            self._output_tail.clear()

    def _register_process(self, process: subprocess.Popen[str]) -> None:
        output_thread = threading.Thread(
            target=self._capture_output,
            args=(process,),
            name=f"{self._process_image_name}-output",
            daemon=True,
        )
        watcher_thread = threading.Thread(
            target=self._watch_process,
            args=(process,),
            name=f"{self._process_image_name}-watcher",
            daemon=True,
        )
        with self._state_lock:
            self._process = process
            self._output_thread = output_thread
            self._watcher_thread = watcher_thread
        output_thread.start()
        watcher_thread.start()

    def _watch_process(self, process: subprocess.Popen[str]) -> None:
        try:
            return_code = process.wait()
        except Exception:  # noqa: BLE001
            self._logger.exception(
                "Failed to wait for %s pid=%s",
                self._process_image_name,
                process.pid,
            )
            return
        with self._state_lock:
            intentional_stop = process.pid in self._stopping_pids
            self._stopping_pids.discard(process.pid)
        if intentional_stop:
            self._logger.info(
                "%s pid=%s stopped with code %s",
                self._process_image_name,
                process.pid,
                return_code,
            )
            return
        if return_code == 0:
            self._logger.info(
                "%s pid=%s exited with code 0",
                self._process_image_name,
                process.pid,
            )
            return
        self._logger.warning(
            "%s pid=%s exited unexpectedly with code %s",
            self._process_image_name,
            process.pid,
            return_code,
        )

    def _capture_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        try:
            for raw_line in process.stdout:
                line = raw_line.rstrip()
                if not line:
                    continue
                with self._state_lock:
                    self._output_tail.append(line)
                self._logger.info("[%s] %s", self._process_image_name, line)
        except Exception:  # noqa: BLE001
            self._logger.exception(
                "Failed to capture output for %s pid=%s",
                self._process_image_name,
                process.pid,
            )

    def _stop_managed_process(self, process: subprocess.Popen[str], *, timeout: float) -> None:
        with self._state_lock:
            self._stopping_pids.add(process.pid)
        if process.poll() is not None:
            self._logger.info(
                "%s pid=%s already stopped with code %s",
                self._process_image_name,
                process.pid,
                process.returncode,
            )
            return
        self._logger.info("Stopping %s pid=%s", self._process_image_name, process.pid)
        process.terminate()
        try:
            process.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            self._logger.warning(
                "%s pid=%s did not stop within %.1fs, killing",
                self._process_image_name,
                process.pid,
                timeout,
            )
        process.kill()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._logger.error(
                "%s pid=%s did not exit after kill within %.1fs",
                self._process_image_name,
                process.pid,
                timeout,
            )

    def _stop_external_process(self, pid: int, *, timeout: float) -> None:
        self._logger.info("Stopping external %s pid=%s", self._process_image_name, pid)
        self._run_taskkill(pid, force=False)
        if self._wait_for_pid_exit(pid, timeout=timeout):
            return
        self._logger.warning(
            "External %s pid=%s did not stop within %.1fs, forcing termination",
            self._process_image_name,
            pid,
            timeout,
        )
        self._run_taskkill(pid, force=True)
        if not self._wait_for_pid_exit(pid, timeout=timeout):
            self._logger.error(
                "External %s pid=%s is still running after forced termination",
                self._process_image_name,
                pid,
            )

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

    def _managed_process_for_pid(self, pid: int) -> subprocess.Popen[str] | None:
        with self._state_lock:
            if self._process is not None and self._process.pid == pid:
                return self._process
        return None

    def _read_last_log_lines(self, limit: int = 15) -> str:
        with self._state_lock:
            tail = list(self._output_tail)[-limit:]
        output = "\n".join(tail)
        return output or self._immediate_exit_message

    def _wait_for_threads(self, *, timeout: float) -> None:
        current_thread = threading.current_thread()
        with self._state_lock:
            threads = [thread for thread in (self._output_thread, self._watcher_thread) if thread is not None]
        for thread in threads:
            if thread is current_thread:
                continue
            thread.join(timeout=timeout)

    def _finalize_process(self) -> None:
        self._cleanup_temp_config()
        current_thread = threading.current_thread()
        with self._state_lock:
            process = self._process
            output_thread = self._output_thread
            watcher_thread = self._watcher_thread
            self._process = None
            self._output_thread = None
            self._watcher_thread = None
        for thread in (output_thread, watcher_thread):
            if thread is None or thread is current_thread:
                continue
            thread.join(timeout=1)
        if process is not None and process.stdout is not None:
            process.stdout.close()

    def _write_temp_config(self, config: dict[str, Any]) -> Path:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix=self._temp_config_prefix,
            delete=False,
        ) as handle:
            json.dump(config, handle, ensure_ascii=False)
        self._temp_config_path = Path(handle.name)
        return self._temp_config_path

    def _cleanup_temp_config(self) -> None:
        if self._temp_config_path is None:
            return
        try:
            self._temp_config_path.unlink(missing_ok=True)
        except OSError:
            pass
        self._temp_config_path = None

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
        return bool(row and row[0].lower() == self._process_image_name.lower())

    @staticmethod
    def _normalize_path(raw_path: str | None) -> str | None:
        if not raw_path:
            return None
        try:
            return str(Path(raw_path).resolve()).lower()
        except OSError:
            return str(Path(raw_path)).lower()

    def _format_running_instances_error(self, instances: list[ProcessInstanceInfo]) -> str:
        lines = [
            f"Уже запущен {self._process_image_name}. Сначала остановите существующий экземпляр."
        ]
        target_path = self._normalize_path(str(self._executable_path))
        for instance in instances[:5]:
            location = instance.executable_path or "путь не определен"
            source = "управляемый клиентом" if instance.executable_path == target_path else "внешний"
            lines.append(f"PID {instance.pid} [{source}] - {location}")
        if len(instances) > 5:
            lines.append(f"И еще экземпляров: {len(instances) - 5}")
        return "\n".join(lines)


class XrayProcessManager(_BaseProcessManager):
    STOP_TIMEOUT_SECONDS = 5.0
    OUTPUT_TAIL_LIMIT = 120

    def __init__(
        self,
        *,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        on_crash_callback: Callable[[], None] | None = None,
    ) -> None:
        self._process_image_name = "xray.exe"
        self._executable_path = XRAY_EXECUTABLE
        self._log_path = XRAY_PROCESS_LOG
        self._temp_config_prefix = "xray-runtime-"
        self._missing_executable_message = "xray.exe не найден."
        self._immediate_exit_message = "Xray завершился сразу после запуска без вывода в лог."
        self._proc: subprocess.Popen[bytes] | None = None
        self._stderr_thread: threading.Thread | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._temp_config_path: Path | None = None
        self._recent_output: deque[str] = deque(maxlen=self.OUTPUT_TAIL_LIMIT)
        self.log_queue: queue.Queue[str] = queue.Queue(maxsize=500)
        self._lock = threading.RLock()
        self._state = State.STOPPED
        self._max_retries = max(0, max_retries)
        self._base_retry_delay = max(0.0, retry_delay)
        self._current_retry_delay = self._base_retry_delay
        self._retries = 0
        self._managed_ports: tuple[int, ...] = ()
        self.on_crash_callback = on_crash_callback
        self._logger = _build_process_logger(
            "vynex_vpn_client.process.xray.exe",
            self._log_path,
        )
        atexit.register(self.stop)

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    def status(self) -> State:
        return self.state

    @property
    def pid(self) -> int | None:
        with self._lock:
            if self._proc is None:
                return None
            return self._proc.pid

    def start(self, config: dict[str, Any]) -> int:
        if not self._executable_path.exists():
            raise FileNotFoundError(self._missing_executable_message)

        ports = self._extract_local_ports(config)

        with self._lock:
            if self._state in {State.STARTING, State.RUNNING, State.STOPPING} and self._proc is not None:
                raise RuntimeError("Xray уже запущен этим клиентом.")

        self._cleanup_temp_config()
        self._clear_output_tail()
        self._retries = 0
        self._current_retry_delay = self._base_retry_delay
        self._managed_ports = ports
        self._write_temp_config(config)

        try:
            for port in ports:
                if not self._check_port(port):
                    raise RuntimeError(f"Локальный порт {port} уже занят другим процессом.")
            if not self._launch(start_watchdog=True):
                raise RuntimeError(self.read_recent_output())
            pid = self.pid
            if pid is None:
                raise RuntimeError(self.read_recent_output())
            return pid
        except Exception:
            self._cleanup_temp_config()
            raise

    def stop(self, pid: int | None = None) -> None:
        with self._lock:
            managed_process = self._proc
            if managed_process is None and not pid:
                self._state = State.STOPPED
                self._cleanup_temp_config()
                return
            self._state = State.STOPPING

        if managed_process is not None:
            self._kill_proc()
        elif pid and is_process_running(pid) and self._is_target_pid(pid):
            self._stop_external_process(pid, timeout=self.STOP_TIMEOUT_SECONDS)

        with self._lock:
            self._proc = None
            self._state = State.STOPPED
            self._retries = 0
            self._current_retry_delay = self._base_retry_delay
            self._managed_ports = ()

        self._cleanup_temp_config()

    def restart(self, config: dict[str, Any]) -> int:
        self.stop(self.pid)
        return self.start(config)

    def is_running(self, pid: int | None) -> bool:
        with self._lock:
            process = self._proc
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
                f"Get-CimInstance Win32_Process -Filter \"Name='{self._process_image_name}'\" | "
                "Select-Object ProcessId, ExecutablePath | ConvertTo-Csv -NoTypeInformation",
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
                "Failed to enumerate %s processes, return code %s",
                self._process_image_name,
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

    def read_recent_output(self, limit: int = 15) -> str:
        with self._lock:
            tail = list(self._recent_output)[-limit:]
        output = "\n".join(tail)
        return output or self._immediate_exit_message

    def collect_output(self, limit: int = 50) -> dict[str, tuple[str, ...]]:
        with self._lock:
            stderr_tail = tuple(list(self._recent_output)[-limit:])
        return {
            "stdout": (),
            "stderr": stderr_tail,
        }

    def iter_logs(self) -> Generator[str, None, None]:
        while True:
            try:
                yield self.log_queue.get_nowait()
            except queue.Empty:
                break

    def _launch(self, *, start_watchdog: bool) -> bool:
        config_path = self._temp_config_path
        if config_path is None:
            return False

        with self._lock:
            self._state = State.STARTING

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._logger.info("Starting %s with config %s", self._process_image_name, config_path)
        try:
            proc = subprocess.Popen(
                [str(self._executable_path), "run", "-c", str(config_path)],
                cwd=str(XRAY_RUNTIME_DIR),
                creationflags=creationflags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError:
            self._logger.exception("Failed to start %s", self._process_image_name)
            with self._lock:
                self._proc = None
                self._state = State.CRASHED
            return False

        stderr_thread = threading.Thread(
            target=self._stderr_reader,
            args=(proc,),
            name="xray-stderr-reader",
            daemon=True,
        )
        stderr_thread.start()

        with self._lock:
            self._proc = proc
            self._stderr_thread = stderr_thread

        time.sleep(1.0)

        with self._lock:
            if self._proc is not proc:
                return False
            if proc.poll() is not None:
                self._proc = None
                if self._state != State.STOPPING:
                    self._state = State.CRASHED
                self._logger.error(
                    "%s exited immediately with code %s",
                    self._process_image_name,
                    proc.returncode,
                )
                self._close_stream(proc)
                return False
            if self._state == State.STOPPING:
                return False
            self._state = State.RUNNING
            if not start_watchdog:
                return True
            if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
                return True
            watchdog_thread = threading.Thread(
                target=self._watchdog,
                name="xray-watchdog",
                daemon=True,
            )
            self._watchdog_thread = watchdog_thread

        watchdog_thread.start()
        return True

    def _kill_proc(self) -> None:
        with self._lock:
            proc = self._proc

        if proc is None:
            return

        self._logger.info("Stopping %s pid=%s", self._process_image_name, proc.pid)
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._logger.warning(
                    "%s pid=%s did not stop within 5.0s, killing",
                    self._process_image_name,
                    proc.pid,
                )
                proc.kill()
                proc.wait()
        except OSError:
            self._logger.exception("Failed to terminate %s pid=%s", self._process_image_name, getattr(proc, "pid", None))
        finally:
            self._close_stream(proc)
            with self._lock:
                if self._proc is proc:
                    self._proc = None

    def _watchdog(self) -> None:
        while True:
            with self._lock:
                proc = self._proc

            if proc is None:
                return

            return_code = proc.wait()
            self._logger.info(
                "%s pid=%s exited with code %s",
                self._process_image_name,
                proc.pid,
                return_code,
            )

            with self._lock:
                if proc is not self._proc:
                    if self._proc is None:
                        return
                    continue
                if self._state == State.STOPPING:
                    self._proc = None
                    self._state = State.STOPPED
                    self._close_stream(proc)
                    return
                self._proc = None
                self._state = State.CRASHED

            self._close_stream(proc)
            if not self._recover_from_crash():
                return

    def _recover_from_crash(self) -> bool:
        callback: Callable[[], None] | None = None

        while True:
            with self._lock:
                if self._state == State.STOPPING:
                    self._state = State.STOPPED
                    return False
                if self._retries >= self._max_retries:
                    self._state = State.STOPPED
                    callback = self.on_crash_callback
                    self._retries = 0
                    self._current_retry_delay = self._base_retry_delay
                    break
                delay = self._current_retry_delay
                self._retries += 1
                self._current_retry_delay *= 2

            time.sleep(delay)

            with self._lock:
                if self._state == State.STOPPING:
                    self._state = State.STOPPED
                    return False
                ports = self._managed_ports

            restart_allowed = True
            for port in ports:
                if not self._check_port(port):
                    restart_allowed = False
                    break
            if not restart_allowed:
                continue

            if self._launch(start_watchdog=False):
                return True

        self._cleanup_temp_config()
        if callback is not None:
            try:
                callback()
            except Exception:
                self._logger.exception("Crash callback failed")
        return False

    def _check_port(self, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.25)
                busy = sock.connect_ex(("127.0.0.1", port)) == 0
        except OSError:
            self._logger.exception("Failed to probe local port %s", port)
            return False

        if not busy:
            return True

        proc = self._find_process_by_port(port)
        if proc is None:
            self._logger.warning("Port %s is busy, but owning process was not identified", port)
            return False

        try:
            process_name = proc.name().lower()
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            self._logger.warning("Port %s is busy, but process details are not accessible", port)
            return False

        if process_name != "xray.exe":
            self._logger.warning("Port %s is occupied by foreign process %s", port, process_name)
            return False

        try:
            self._logger.warning("Killing stale xray.exe pid=%s on port %s", proc.pid, port)
            proc.kill()
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            self._logger.exception("Failed to kill stale xray.exe on port %s", port)
            return False

        time.sleep(1.0)
        return True

    def _find_process_by_port(self, port: int) -> psutil.Process | None:
        try:
            connections = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, OSError):
            self._logger.exception("Failed to inspect port ownership for %s", port)
            return None

        for connection in connections:
            local_port = self._connection_port(connection)
            local_host = self._connection_host(connection)
            if local_port != port or local_host not in {"127.0.0.1", "0.0.0.0", "::1", None}:
                continue
            if connection.pid is None:
                continue
            try:
                return psutil.Process(connection.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                return None
        return None

    def _stderr_reader(self, proc: subprocess.Popen[bytes]) -> None:
        stderr_stream = proc.stderr
        if stderr_stream is None:
            return

        try:
            for raw_line in iter(stderr_stream.readline, b""):
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line:
                    continue
                with self._lock:
                    self._recent_output.append(line)
                self._logger.info("[xray] %s", line)
                try:
                    self.log_queue.put_nowait(line)
                except queue.Full:
                    try:
                        self.log_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self.log_queue.put_nowait(line)
                    except queue.Full:
                        pass
        except OSError:
            self._logger.exception("Failed to read xray.exe stderr")

    def _extract_local_ports(self, config: dict[str, Any]) -> tuple[int, ...]:
        ports: list[int] = []
        for inbound in config.get("inbounds", []):
            if not isinstance(inbound, dict):
                continue
            listen = str(inbound.get("listen", "127.0.0.1") or "127.0.0.1")
            port = inbound.get("port")
            if listen != "127.0.0.1":
                continue
            if isinstance(port, int):
                ports.append(port)
        return tuple(ports)

    def _clear_output_tail(self) -> None:
        with self._lock:
            self._recent_output.clear()
        while True:
            try:
                self.log_queue.get_nowait()
            except queue.Empty:
                break

    def _write_temp_config(self, config: dict[str, Any]) -> Path:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix=self._temp_config_prefix,
            delete=False,
        ) as handle:
            json.dump(config, handle, ensure_ascii=False)
        self._temp_config_path = Path(handle.name)
        return self._temp_config_path

    def _cleanup_temp_config(self) -> None:
        if self._temp_config_path is None:
            return
        try:
            self._temp_config_path.unlink(missing_ok=True)
        except OSError:
            pass
        self._temp_config_path = None

    def _stop_external_process(self, pid: int, *, timeout: float) -> None:
        self._logger.info("Stopping external %s pid=%s", self._process_image_name, pid)
        self._run_taskkill(pid, force=False)
        if self._wait_for_pid_exit(pid, timeout=timeout):
            return
        self._logger.warning(
            "External %s pid=%s did not stop within %.1fs, forcing termination",
            self._process_image_name,
            pid,
            timeout,
        )
        self._run_taskkill(pid, force=True)
        if not self._wait_for_pid_exit(pid, timeout=timeout):
            self._logger.error(
                "External %s pid=%s is still running after forced termination",
                self._process_image_name,
                pid,
            )

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
        return bool(row and row[0].lower() == self._process_image_name.lower())

    @staticmethod
    def _normalize_path(raw_path: str | None) -> str | None:
        if not raw_path:
            return None
        try:
            return str(Path(raw_path).resolve()).lower()
        except OSError:
            return str(Path(raw_path)).lower()

    def _format_running_instances_error(self, instances: list[ProcessInstanceInfo]) -> str:
        lines = [
            f"Уже запущен {self._process_image_name}. Сначала остановите существующий экземпляр."
        ]
        target_path = self._normalize_path(str(self._executable_path))
        for instance in instances[:5]:
            location = instance.executable_path or "путь не определен"
            source = "управляемый клиентом" if instance.executable_path == target_path else "внешний"
            lines.append(f"PID {instance.pid} [{source}] - {location}")
        if len(instances) > 5:
            lines.append(f"И еще экземпляров: {len(instances) - 5}")
        return "\n".join(lines)

    @staticmethod
    def _connection_port(connection: object) -> int | None:
        local_address = getattr(connection, "laddr", None)
        if local_address is None:
            return None
        if hasattr(local_address, "port"):
            return int(local_address.port)
        if isinstance(local_address, tuple) and len(local_address) >= 2:
            return int(local_address[1])
        return None

    @staticmethod
    def _connection_host(connection: object) -> str | None:
        local_address = getattr(connection, "laddr", None)
        if local_address is None:
            return None
        if hasattr(local_address, "ip"):
            return str(local_address.ip)
        if isinstance(local_address, tuple) and local_address:
            return str(local_address[0])
        return None

    @staticmethod
    def _close_stream(proc: subprocess.Popen[bytes]) -> None:
        stderr_stream = getattr(proc, "stderr", None)
        if stderr_stream is None:
            return
        try:
            stderr_stream.close()
        except OSError:
            pass


class SingboxProcessManager(_BaseProcessManager):
    def __init__(self) -> None:
        super().__init__(
            process_image_name="sing-box.exe",
            executable_path=SINGBOX_EXECUTABLE,
            log_path=SINGBOX_PROCESS_LOG,
            temp_config_prefix="sing-box-runtime-",
            missing_executable_message="sing-box.exe не найден.",
            immediate_exit_message="sing-box завершился сразу после запуска без вывода в лог.",
        )
