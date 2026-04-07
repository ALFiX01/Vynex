from __future__ import annotations

import csv
from collections import deque
from dataclasses import dataclass
import json
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .constants import XRAY_EXECUTABLE, XRAY_RUNTIME_DIR
from .utils import is_process_running


@dataclass(frozen=True)
class XrayInstanceInfo:
    pid: int
    executable_path: str | None = None


class XrayProcessManager:
    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._output_tail: deque[str] = deque(maxlen=120)
        self._output_thread: threading.Thread | None = None
        self._temp_config_path: Path | None = None

    def start(self, config: dict[str, Any]) -> int:
        if not XRAY_EXECUTABLE.exists():
            raise FileNotFoundError("xray.exe не найден.")
        self.ensure_no_running_instances()
        config_path = self._write_temp_config(config)
        self._output_tail.clear()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = subprocess.Popen(
            [str(XRAY_EXECUTABLE), "run", "-c", str(config_path)],
            cwd=str(XRAY_RUNTIME_DIR),
            creationflags=creationflags,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        self._start_output_reader()
        time.sleep(1.5)
        if self._process.poll() is not None:
            self._cleanup_temp_config()
            self._finalize_process()
            raise RuntimeError(self._read_last_log_lines())
        self._cleanup_temp_config()
        return int(self._process.pid)

    def stop(self, pid: int | None) -> None:
        if not pid:
            return
        if self._process and self._process.pid == pid and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
                self._finalize_process()
                return
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
                self._finalize_process()
                return
        if not is_process_running(pid) or not self._is_xray_pid(pid):
            self._finalize_process()
            return
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self._finalize_process()

    @staticmethod
    def is_running(pid: int | None) -> bool:
        return bool(pid and is_process_running(pid) and XrayProcessManager._is_xray_pid(pid))

    def ensure_no_running_instances(self, *, exclude_pid: int | None = None) -> None:
        running_instances = [
            instance
            for instance in self.list_running_instances()
            if exclude_pid is None or instance.pid != exclude_pid
        ]
        if not running_instances:
            return
        raise RuntimeError(self._format_running_instances_error(running_instances))

    def list_running_instances(self) -> list[XrayInstanceInfo]:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='xray.exe'\" | "
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
            return []
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if len(lines) <= 1:
            return []
        instances: list[XrayInstanceInfo] = []
        for row in csv.DictReader(lines):
            raw_pid = (row.get("ProcessId") or "").strip()
            if not raw_pid.isdigit():
                continue
            raw_path = (row.get("ExecutablePath") or "").strip() or None
            instances.append(
                XrayInstanceInfo(
                    pid=int(raw_pid),
                    executable_path=self._normalize_path(raw_path),
                )
            )
        return instances

    def _read_last_log_lines(self, limit: int = 15) -> str:
        tail = "\n".join(list(self._output_tail)[-limit:])
        return tail or "Xray завершился сразу после запуска без вывода в лог."

    def read_recent_output(self, limit: int = 15) -> str:
        return self._read_last_log_lines(limit)

    @staticmethod
    def _is_xray_pid(pid: int) -> bool:
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
        return bool(row and row[0].lower() == "xray.exe")

    @staticmethod
    def _normalize_path(raw_path: str | None) -> str | None:
        if not raw_path:
            return None
        try:
            return str(Path(raw_path).resolve()).lower()
        except OSError:
            return str(Path(raw_path)).lower()

    @staticmethod
    def _format_running_instances_error(instances: list[XrayInstanceInfo]) -> str:
        lines = ["Уже запущен xray.exe. Сначала остановите существующий экземпляр."]
        target_path = XrayProcessManager._normalize_path(str(XRAY_EXECUTABLE))
        for instance in instances[:5]:
            location = instance.executable_path or "путь не определен"
            source = "управляемый клиентом" if instance.executable_path == target_path else "внешний"
            lines.append(f"PID {instance.pid} [{source}] - {location}")
        if len(instances) > 5:
            lines.append(f"И еще экземпляров: {len(instances) - 5}")
        return "\n".join(lines)

    def _start_output_reader(self) -> None:
        if self._process is None or self._process.stdout is None:
            return
        self._output_thread = threading.Thread(
            target=self._capture_output,
            args=(self._process,),
            daemon=True,
        )
        self._output_thread.start()

    def _capture_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            self._output_tail.append(line.rstrip())

    def _finalize_process(self) -> None:
        self._cleanup_temp_config()
        if self._output_thread is not None:
            self._output_thread.join(timeout=1)
            self._output_thread = None
        if self._process is not None and self._process.stdout is not None:
            self._process.stdout.close()
        self._process = None

    def _write_temp_config(self, config: dict[str, Any]) -> Path:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix="xray-runtime-",
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
