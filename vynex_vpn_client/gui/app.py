from __future__ import annotations

import ctypes
import sys

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from vynex_vpn_client.constants import APP_NAME, APP_VERSION

from . import design_tokens as tokens
from .main_window import MainWindow


def _set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            f"Vynex.VPNClient.{APP_VERSION}"
        )
    except Exception:
        pass


def _apply_base_style(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setFont(QFont(tokens.FONT_FAMILY, tokens.FONT_POINT_SIZE))

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(tokens.COLOR_BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(tokens.COLOR_TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Base, QColor(tokens.COLOR_SURFACE_ALT))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(tokens.COLOR_SURFACE))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(tokens.COLOR_SURFACE))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(tokens.COLOR_TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Text, QColor(tokens.COLOR_TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Button, QColor(tokens.COLOR_SURFACE))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(tokens.COLOR_TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(tokens.COLOR_TEXT_INVERSE))
    palette.setColor(QPalette.ColorRole.Link, QColor(tokens.COLOR_PRIMARY))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(tokens.COLOR_SELECTION))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(tokens.COLOR_TEXT_INVERSE))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(tokens.COLOR_TEXT_DISABLED))
    app.setPalette(palette)

    app.setStyleSheet(tokens.app_stylesheet())


def run_gui(argv: list[str] | None = None) -> int:
    _set_windows_app_user_model_id()
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("Vynex")
    if QSystemTrayIcon.isSystemTrayAvailable():
        app.setQuitOnLastWindowClosed(False)
    _apply_base_style(app)

    window = MainWindow()
    window.show()
    return app.exec()


def main() -> int:
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
