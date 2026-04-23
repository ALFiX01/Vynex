from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import main
from vynex_vpn_client.constants import DEFAULT_CONSOLE_COLUMNS, DEFAULT_CONSOLE_LINES


def test_set_console_window_size_uses_mode_con_on_windows_tty() -> None:
    with (
        patch.object(main.sys, "platform", "win32"),
        patch.object(main.sys, "stdout", SimpleNamespace(isatty=lambda: True)),
        patch("main.os.system") as system_mock,
    ):
        main._set_console_window_size()

    system_mock.assert_called_once_with(
        f"mode con cols={DEFAULT_CONSOLE_COLUMNS} lines={DEFAULT_CONSOLE_LINES} > nul"
    )


def test_ensure_running_as_admin_skips_on_non_windows() -> None:
    with patch.object(main.sys, "platform", "linux"):
        main._ensure_running_as_admin()


def test_ensure_running_as_admin_relaunches_script_with_runas() -> None:
    script_path = str(main.Path(main.__file__).resolve())
    working_directory = "C:\\Users\\Daniil\\Documents\\GitHub\\Vynex"
    expected_parameters = subprocess.list2cmdline([script_path, "--debug"])

    with (
        patch.object(main.sys, "platform", "win32"),
        patch.object(main.sys, "executable", "C:\\Python\\python.exe"),
        patch.object(main.sys, "argv", [script_path, "--debug"]),
        patch.object(main.sys, "frozen", False, create=True),
        patch("main.Path.cwd", return_value=main.Path(working_directory)),
        patch("main.ctypes.windll", create=True) as windll_mock,
    ):
        windll_mock.shell32.IsUserAnAdmin.return_value = 0
        windll_mock.shell32.ShellExecuteW.return_value = 42

        try:
            main._ensure_running_as_admin()
        except SystemExit as exc:
            assert exc.code == 0
        else:
            raise AssertionError("Expected SystemExit after elevation relaunch")

    windll_mock.shell32.ShellExecuteW.assert_called_once_with(
        None,
        "runas",
        "C:\\Python\\python.exe",
        expected_parameters,
        working_directory,
        1,
    )


def test_ensure_running_as_admin_skips_when_already_elevated() -> None:
    with (
        patch.object(main.sys, "platform", "win32"),
        patch("main.ctypes.windll", create=True) as windll_mock,
    ):
        windll_mock.shell32.IsUserAnAdmin.return_value = 1

        main._ensure_running_as_admin()

    windll_mock.shell32.ShellExecuteW.assert_not_called()
