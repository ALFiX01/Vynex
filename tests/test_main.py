from __future__ import annotations

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
