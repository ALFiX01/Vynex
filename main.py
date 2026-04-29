from __future__ import annotations

import ctypes
import importlib
import os
import subprocess
import sys
from pathlib import Path

from vynex_vpn_client.constants import APP_NAME, APP_VERSION


def _project_venv_python() -> Path | None:
    root = Path(__file__).resolve().parent
    if sys.platform == "win32":
        candidate = root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = root / ".venv" / "bin" / "python"
    return candidate if candidate.exists() else None


def _is_running_as_admin() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _admin_relaunch_command() -> tuple[str, str | None]:
    executable = str(Path(sys.executable).resolve())
    if getattr(sys, "frozen", False):
        arguments = sys.argv[1:]
    else:
        arguments = [str(Path(__file__).resolve()), *sys.argv[1:]]
    parameters = subprocess.list2cmdline(arguments)
    return executable, parameters or None


def _ensure_running_as_admin() -> None:
    if sys.platform != "win32" or _is_running_as_admin():
        return
    executable, parameters = _admin_relaunch_command()
    working_directory = str(Path.cwd())
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            executable,
            parameters,
            working_directory,
            1,
        )
    except Exception as exc:
        print("Не удалось перезапустить приложение с правами администратора.", file=sys.stderr)
        raise SystemExit(1) from exc
    if result <= 32:
        print("Запуск от имени администратора был отменен или завершился ошибкой.", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(0)


def _print_missing_dependency(error: ModuleNotFoundError) -> None:
    python_executable = Path(sys.executable).resolve()
    venv_python = _project_venv_python()
    print(f"Missing Python dependency: {error.name}", file=sys.stderr)
    print(f"Current interpreter: {python_executable}", file=sys.stderr)
    if venv_python is not None:
        print(f"Project virtualenv:  {venv_python}", file=sys.stderr)
    print(file=sys.stderr)
    print("Start Vynex with the virtualenv interpreter.", file=sys.stderr)
    if venv_python is not None:
        print(f"Example: & {venv_python} {Path(__file__).resolve()}", file=sys.stderr)
    else:
        print("Example: .\\.venv\\Scripts\\Activate.ps1 ; python main.py", file=sys.stderr)


def _maybe_reexec_with_project_venv(error: ModuleNotFoundError) -> None:
    if os.environ.get("VYNEX_SKIP_VENV_REEXEC") == "1":
        _print_missing_dependency(error)
        raise SystemExit(1)
    venv_python = _project_venv_python()
    if venv_python is None:
        _print_missing_dependency(error)
        raise SystemExit(1)
    try:
        current_python = Path(sys.executable).resolve()
        target_python = venv_python.resolve()
    except OSError:
        current_python = Path(sys.executable)
        target_python = venv_python
    if current_python == target_python:
        _print_missing_dependency(error)
        raise SystemExit(1)
    env = os.environ.copy()
    env["VYNEX_SKIP_VENV_REEXEC"] = "1"
    os.execve(
        str(target_python),
        [str(target_python), str(Path(__file__).resolve()), *sys.argv[1:]],
        env,
    )


def _is_legacy_terminal_requested(argv: list[str] | None = None) -> bool:
    args = list(sys.argv[1:] if argv is None else argv)
    return "--terminal" in args or "--legacy-terminal" in args


def _entrypoint_module_name(argv: list[str] | None = None) -> str:
    if _is_legacy_terminal_requested(argv):
        return "vynex_vpn_client.app"
    return "vynex_vpn_client.gui.app"


def _load_entrypoint(argv: list[str] | None = None):
    module_name = _entrypoint_module_name(argv)
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        _maybe_reexec_with_project_venv(error)
        raise
    return module.main


def _set_console_title() -> None:
    if sys.platform != "win32":
        return
    title = f"{APP_NAME} v{APP_VERSION}"
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception:
        pass


def _set_console_window_size() -> None:
    if sys.platform != "win32" or not sys.stdout.isatty():
        return
    from vynex_vpn_client.constants import DEFAULT_CONSOLE_COLUMNS, DEFAULT_CONSOLE_LINES

    try:
        os.system(f"mode con cols={DEFAULT_CONSOLE_COLUMNS} lines={DEFAULT_CONSOLE_LINES} > nul")
    except Exception:
        pass


def main() -> int:
    entrypoint = _load_entrypoint()
    return int(entrypoint())


if __name__ == "__main__":
    if _is_legacy_terminal_requested():
        _set_console_window_size()
        _set_console_title()
    raise SystemExit(main())
