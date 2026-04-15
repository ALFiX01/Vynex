from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from vynex_vpn_client.core import XrayInstaller


def test_ensure_xray_tun_runtime_updates_when_wintun_missing(tmp_path: Path) -> None:
    xray_path = tmp_path / "xray.exe"
    wintun_path = tmp_path / "wintun.dll"
    xray_path.write_bytes(b"")

    installer = XrayInstaller()

    def _update_xray() -> Path:
        wintun_path.write_bytes(b"dll")
        return xray_path

    with (
        patch("vynex_vpn_client.core.XRAY_EXECUTABLE", xray_path),
        patch("vynex_vpn_client.core.WINTUN_DLL", wintun_path),
        patch.object(XrayInstaller, "ensure_xray", return_value=xray_path),
        patch.object(XrayInstaller, "update_xray", side_effect=_update_xray) as update_xray,
        patch.object(XrayInstaller, "get_xray_version", return_value=(26, 3, 27)),
    ):
        assert installer.ensure_xray_tun_runtime() == xray_path

    update_xray.assert_called_once()


def test_ensure_xray_tun_runtime_raises_when_version_is_too_old(tmp_path: Path) -> None:
    xray_path = tmp_path / "xray.exe"
    wintun_path = tmp_path / "wintun.dll"
    xray_path.write_bytes(b"")
    wintun_path.write_bytes(b"dll")
    installer = XrayInstaller()

    with (
        patch("vynex_vpn_client.core.XRAY_EXECUTABLE", xray_path),
        patch("vynex_vpn_client.core.WINTUN_DLL", wintun_path),
        patch.object(XrayInstaller, "ensure_xray", return_value=xray_path),
        patch.object(XrayInstaller, "update_xray", return_value=xray_path) as update_xray,
        patch.object(XrayInstaller, "get_xray_version", side_effect=[(25, 9, 1), (25, 9, 1)]),
    ):
        try:
            installer.ensure_xray_tun_runtime()
        except RuntimeError as exc:
            assert "не поддерживает TUN режим" in str(exc)
        else:
            raise AssertionError("Expected RuntimeError for unsupported xray version")

    update_xray.assert_called_once()
