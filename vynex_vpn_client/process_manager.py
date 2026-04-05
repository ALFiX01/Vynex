from __future__ import annotations

import csv
from dataclasses import dataclass
import subprocess
import time
from pathlib import Path

from .constants import XRAY_EXECUTABLE, XRAY_RUNTIME_DIR, XRAY_STDOUT_LOG
from .utils import is_process_running


@dataclass(frozen=True)
class XrayInstanceInfo:
    pid: int
    executable_path: str | None = None


class XrayProcessManager:
    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._log_file = None

    def start(self, config_path: Path) -> int:
        if not XRAY_EXECUTABLE.exists():
            raise FileNotFoundError("xray.exe не найден.")
        self.ensure_no_running_instances()
        XRAY_STDOUT_LOG.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = XRAY_STDOUT_LOG.open("w", encoding="utf-8")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = subprocess.Popen(
            [str(XRAY_EXECUTABLE), "run", "-c", str(config_path)],
            cwd=str(XRAY_RUNTIME_DIR),
            creationflags=creationflags,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
        )
        time.sleep(1.5)
        if self._process.poll() is not None:
            self._close_log_file()
            raise RuntimeError(self._read_last_log_lines())
        return int(self._process.pid)

    def stop(self, pid: int | None) -> None:
        if not pid:
            return
        if self._process and self._process.pid == pid and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
                self._process = None
                self._close_log_file()
                return
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
                self._process = None
                self._close_log_file()
                return
        if not is_process_running(pid) or not self._is_xray_pid(pid):
            self._close_log_file()
            return
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self._process = None
        self._close_log_file()

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

    @staticmethod
    def _read_last_log_lines(limit: int = 15) -> str:
        if not XRAY_STDOUT_LOG.exists():
            return "Xray завершился сразу после запуска без вывода в лог."
        lines = XRAY_STDOUT_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
        tail = "\n".join(lines[-limit:])
        return tail or "Xray завершился сразу после запуска без вывода в лог."

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

    def _close_log_file(self) -> None:
        if self._log_file:
            self._log_file.close()
            self._log_file = None
