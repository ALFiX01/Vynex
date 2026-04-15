from __future__ import annotations

import ctypes
import sys

from vynex_vpn_client.app import main
from vynex_vpn_client.constants import APP_NAME, APP_VERSION


def _set_console_title() -> None:
    if sys.platform != "win32":
        return
    title = f"{APP_NAME} v{APP_VERSION}"
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception:
        pass


if __name__ == "__main__":
    _set_console_title()
    raise SystemExit(main())