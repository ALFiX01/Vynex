from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable

import requests

from .app_update import AppReleaseInfo
from .constants import (
    APP_UPDATE_ASSET_NAME,
    APP_UPDATE_BACKUP_BASENAME_SUFFIX,
    APP_UPDATE_DOWNLOAD_CHUNK_SIZE,
    APP_UPDATE_DOWNLOAD_TIMEOUT_SECONDS,
    APP_UPDATE_HELPER_RETRY_COUNT,
    APP_UPDATE_HELPER_SCRIPT_NAME,
    APP_UPDATE_HELPER_WAIT_SECONDS,
    APP_UPDATE_TEMP_SUFFIX,
    APP_UPDATES_DIR,
)


ProgressCallback = Callable[[int, int | None], None]


@dataclass(frozen=True)
class AppUpdateDownload:
    release: AppReleaseInfo
    staged_executable: Path
    expected_size: int | None = None


@dataclass(frozen=True)
class AppUpdateApplyPlan:
    current_pid: int
    current_executable: Path
    staged_executable: Path
    backup_executable: Path
    helper_script: Path
    release_version: str | None = None


class AppSelfUpdater:
    def __init__(self, *, updates_dir: Path = APP_UPDATES_DIR, session: requests.Session | None = None) -> None:
        self.updates_dir = updates_dir
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/octet-stream, application/vnd.github+json",
                "User-Agent": "Vynex-Client/1.0",
            }
        )

    def can_self_update(self) -> bool:
        return bool(sys.platform == "win32" and getattr(sys, "frozen", False) and self._is_windows_executable(Path(sys.executable)))

    def current_executable(self) -> Path:
        if sys.platform != "win32":
            raise RuntimeError("Self-update поддерживается только в Windows-сборке приложения.")
        if not getattr(sys, "frozen", False):
            raise RuntimeError("Self-update доступен только в packaged Windows build. Проверка обновлений продолжит работать.")
        current_executable = Path(sys.executable).resolve()
        if not self._is_windows_executable(current_executable):
            raise RuntimeError("Текущий запуск не является Windows .exe сборкой приложения.")
        if not current_executable.exists():
            raise RuntimeError(f"Текущий exe не найден: {current_executable}")
        return current_executable

    def download_release(
        self,
        release_info: AppReleaseInfo,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> AppUpdateDownload:
        if not release_info.has_installable_asset:
            raise RuntimeError("В latest release отсутствует exe-asset приложения.")

        self.updates_dir.mkdir(parents=True, exist_ok=True)
        target_path = self._staged_executable_path(release_info)
        part_path = self._part_path(target_path)
        expected_size = release_info.asset_size if release_info.asset_size and release_info.asset_size > 0 else None

        if target_path.exists():
            current_size = target_path.stat().st_size
            if current_size > 0 and (expected_size is None or current_size == expected_size):
                return AppUpdateDownload(release=release_info, staged_executable=target_path, expected_size=expected_size)
            target_path.unlink(missing_ok=True)

        part_path.unlink(missing_ok=True)

        bytes_downloaded = 0
        try:
            with self.session.get(
                release_info.asset_download_url,
                stream=True,
                timeout=APP_UPDATE_DOWNLOAD_TIMEOUT_SECONDS,
            ) as response:
                response.raise_for_status()
                with part_path.open("wb") as file_obj:
                    for chunk in response.iter_content(chunk_size=APP_UPDATE_DOWNLOAD_CHUNK_SIZE):
                        if not chunk:
                            continue
                        file_obj.write(chunk)
                        bytes_downloaded += len(chunk)
                        if progress_callback is not None:
                            progress_callback(bytes_downloaded, expected_size)
        except requests.Timeout as exc:
            part_path.unlink(missing_ok=True)
            raise RuntimeError("Не удалось скачать обновление: превышено время ожидания.") from exc
        except requests.RequestException as exc:
            part_path.unlink(missing_ok=True)
            raise RuntimeError(f"Не удалось скачать обновление приложения: {exc}") from exc
        except OSError as exc:
            part_path.unlink(missing_ok=True)
            raise RuntimeError(f"Не удалось сохранить обновление приложения в {part_path.parent}.") from exc

        if bytes_downloaded <= 0:
            part_path.unlink(missing_ok=True)
            raise RuntimeError("Файл обновления скачался пустым.")
        if expected_size is not None and bytes_downloaded != expected_size:
            part_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Размер скачанного файла не совпадает с release asset: ожидалось {expected_size} байт, получено {bytes_downloaded}."
            )

        try:
            part_path.replace(target_path)
        except OSError as exc:
            part_path.unlink(missing_ok=True)
            raise RuntimeError("Не удалось завершить подготовку файла обновления.") from exc

        final_size = target_path.stat().st_size
        if final_size <= 0:
            target_path.unlink(missing_ok=True)
            raise RuntimeError("Файл обновления сохранился пустым.")
        if expected_size is not None and final_size != expected_size:
            target_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Размер сохраненного файла не совпадает с release asset: ожидалось {expected_size} байт, получено {final_size}."
            )
        return AppUpdateDownload(release=release_info, staged_executable=target_path, expected_size=expected_size)

    def prepare_apply_plan(
        self,
        download: AppUpdateDownload,
        *,
        current_pid: int | None = None,
        current_executable: Path | None = None,
    ) -> AppUpdateApplyPlan:
        target_executable = Path(current_executable).resolve() if current_executable is not None else self.current_executable()
        if not download.staged_executable.exists():
            raise RuntimeError(f"Файл обновления не найден: {download.staged_executable}")
        if download.staged_executable.resolve() == target_executable:
            raise RuntimeError("Нельзя применять обновление поверх уже запущенного exe без staging-файла.")
        pid = int(current_pid or os.getpid())
        helper_script = self.updates_dir / self._helper_script_filename(pid)
        backup_name = f"{target_executable.stem}{APP_UPDATE_BACKUP_BASENAME_SUFFIX}{target_executable.suffix}"
        return AppUpdateApplyPlan(
            current_pid=pid,
            current_executable=target_executable,
            staged_executable=download.staged_executable.resolve(),
            backup_executable=target_executable.with_name(backup_name),
            helper_script=helper_script,
            release_version=download.release.latest_version,
        )

    def prepare_update(
        self,
        release_info: AppReleaseInfo,
        *,
        current_pid: int | None = None,
        current_executable: Path | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> AppUpdateApplyPlan:
        download = self.download_release(release_info, progress_callback=progress_callback)
        plan = self.prepare_apply_plan(download, current_pid=current_pid, current_executable=current_executable)
        self.write_helper_script(plan)
        return plan

    def write_helper_script(self, plan: AppUpdateApplyPlan) -> Path:
        self.updates_dir.mkdir(parents=True, exist_ok=True)
        script_content = self.generate_helper_script(plan)
        try:
            with plan.helper_script.open("w", encoding="utf-8-sig", newline="\r\n") as file_obj:
                file_obj.write(script_content)
        except OSError as exc:
            raise RuntimeError(f"Не удалось создать helper script обновления: {plan.helper_script}") from exc
        return plan.helper_script

    def launch_helper(self, plan: AppUpdateApplyPlan) -> None:
        if not plan.helper_script.exists():
            raise RuntimeError(f"Helper script обновления не найден: {plan.helper_script}")
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.Popen(
                [comspec, "/c", str(plan.helper_script)],
                cwd=str(self.updates_dir),
                close_fds=True,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise RuntimeError("Не удалось запустить helper script обновления.") from exc

    def generate_helper_script(self, plan: AppUpdateApplyPlan) -> str:
        values = {
            "current_pid": str(plan.current_pid),
            "target": self._escape_batch_value(plan.current_executable),
            "staged": self._escape_batch_value(plan.staged_executable),
            "backup": self._escape_batch_value(plan.backup_executable),
            "script": self._escape_batch_value(plan.helper_script),
            "wait_seconds": str(APP_UPDATE_HELPER_WAIT_SECONDS),
            "retry_count": str(APP_UPDATE_HELPER_RETRY_COUNT),
        }
        return """@echo off
chcp 65001>nul
setlocal EnableExtensions
set "CURRENT_PID={current_pid}"
set "TARGET_EXE={target}"
set "STAGED_EXE={staged}"
set "BACKUP_EXE={backup}"
set "SCRIPT_PATH={script}"
set "WAIT_SECONDS={wait_seconds}"
set "RETRY_COUNT={retry_count}"

call :wait_for_process_exit
if errorlevel 1 goto :fail

call :delete_if_exists "%BACKUP_EXE%"
if errorlevel 1 goto :fail

call :replace_executable
if errorlevel 1 goto :fail

start "" "%TARGET_EXE%"
if errorlevel 1 (
  call :restore_backup
  goto :fail
)

if exist "%STAGED_EXE%" del /f /q "%STAGED_EXE%" >nul 2>&1
del /f /q "%SCRIPT_PATH%" >nul 2>&1
exit /b 0

:wait_for_process_exit
set /a WAIT_ATTEMPTS=0
:wait_loop
tasklist /FI "PID eq %CURRENT_PID%" 2>nul | find /I "%CURRENT_PID%" >nul
if errorlevel 1 exit /b 0
set /a WAIT_ATTEMPTS+=1
if %WAIT_ATTEMPTS% geq %WAIT_SECONDS% exit /b 1
timeout /T 1 /NOBREAK >nul
goto wait_loop

:delete_if_exists
set "DELETE_TARGET=%~1"
if not exist "%DELETE_TARGET%" exit /b 0
set /a DELETE_ATTEMPTS=0
:delete_loop
del /f /q "%DELETE_TARGET%" >nul 2>&1
if not exist "%DELETE_TARGET%" exit /b 0
set /a DELETE_ATTEMPTS+=1
if %DELETE_ATTEMPTS% geq %RETRY_COUNT% exit /b 1
timeout /T 1 /NOBREAK >nul
goto delete_loop

:replace_executable
set /a REPLACE_ATTEMPTS=0
:replace_loop
if exist "%TARGET_EXE%" (
  move /Y "%TARGET_EXE%" "%BACKUP_EXE%" >nul 2>&1
  if exist "%TARGET_EXE%" goto :replace_retry
)
move /Y "%STAGED_EXE%" "%TARGET_EXE%" >nul 2>&1
if exist "%TARGET_EXE%" exit /b 0
call :restore_backup >nul 2>&1
:replace_retry
set /a REPLACE_ATTEMPTS+=1
if %REPLACE_ATTEMPTS% geq %RETRY_COUNT% exit /b 1
timeout /T 1 /NOBREAK >nul
goto replace_loop

:restore_backup
if exist "%BACKUP_EXE%" if not exist "%TARGET_EXE%" move /Y "%BACKUP_EXE%" "%TARGET_EXE%" >nul 2>&1
exit /b 0

:fail
del /f /q "%SCRIPT_PATH%" >nul 2>&1
exit /b 1
""".format(**values)

    @staticmethod
    def _is_windows_executable(path: Path) -> bool:
        return path.suffix.lower() == ".exe"

    @staticmethod
    def _escape_batch_value(value: Path | str) -> str:
        return str(value).replace("%", "%%")

    @staticmethod
    def _part_path(target_path: Path) -> Path:
        return target_path.with_suffix(f"{target_path.suffix}{APP_UPDATE_TEMP_SUFFIX}")

    @staticmethod
    def _helper_script_filename(pid: int) -> str:
        helper_name = Path(APP_UPDATE_HELPER_SCRIPT_NAME)
        return f"{helper_name.stem}-{pid}{helper_name.suffix}"

    @staticmethod
    def _sanitize_version_for_filename(version: str | None) -> str:
        normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", str(version or "").strip())
        normalized = normalized.strip(".-")
        return normalized or "latest"

    def _staged_executable_path(self, release_info: AppReleaseInfo) -> Path:
        asset_name = Path(APP_UPDATE_ASSET_NAME)
        version_label = self._sanitize_version_for_filename(release_info.latest_version)
        return self.updates_dir / f"{asset_name.stem}-{version_label}{asset_name.suffix}"
