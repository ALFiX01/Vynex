from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from vynex_vpn_client.gui.main_window import MainWindow  # noqa: E402


def test_short_datetime_formats_iso_values() -> None:
    assert MainWindow._short_datetime("2026-04-24T14:15:16+00:00") == "2026-04-24 14:15:16"
    assert MainWindow._short_datetime("2026-04-24T14:15:16Z") == "2026-04-24 14:15:16"
    assert MainWindow._short_datetime("") == "-"
