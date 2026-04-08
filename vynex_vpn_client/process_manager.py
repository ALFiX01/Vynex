from __future__ import annotations

import csv
from collections import deque
from dataclasses import dataclass
import json
import logging
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

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
    def __init__(self) -> None:
        super().__init__(
            process_image_name="xray.exe",
            executable_path=XRAY_EXECUTABLE,
            log_path=XRAY_PROCESS_LOG,
            temp_config_prefix="xray-runtime-",
            missing_executable_message="xray.exe не найден.",
            immediate_exit_message="Xray завершился сразу после запуска без вывода в лог.",
        )


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
