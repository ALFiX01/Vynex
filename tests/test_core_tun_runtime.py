from __future__ import annotations

import zipfile
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


def test_ensure_amneziawg_runtime_updates_when_runtime_is_incomplete(tmp_path: Path) -> None:
    amneziawg_path = tmp_path / "amneziawg.exe"
    awg_path = tmp_path / "awg.exe"
    wintun_path = tmp_path / "wintun.dll"
    installer = XrayInstaller()

    def _update_amneziawg() -> Path:
        amneziawg_path.write_bytes(b"awg-main")
        awg_path.write_bytes(b"awg-fallback")
        wintun_path.write_bytes(b"dll")
        return amneziawg_path

    with (
        patch("vynex_vpn_client.core.AMNEZIAWG_EXECUTABLE", amneziawg_path),
        patch("vynex_vpn_client.core.AMNEZIAWG_EXECUTABLE_FALLBACK", awg_path),
        patch("vynex_vpn_client.core.AMNEZIAWG_WINTUN_DLL", wintun_path),
        patch.object(XrayInstaller, "update_amneziawg", side_effect=_update_amneziawg) as update_amneziawg,
    ):
        assert installer.ensure_amneziawg_runtime() == amneziawg_path

    update_amneziawg.assert_called_once()


def test_extract_wintun_release_extracts_amd64_dll(tmp_path: Path) -> None:
    installer = XrayInstaller()
    archive_path = tmp_path / "wintun.zip"
    target_path = tmp_path / "runtime" / "wintun.dll"

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("wintun/bin/x86/wintun.dll", b"x86")
        archive.writestr("wintun/bin/amd64/wintun.dll", b"amd64")

    installer._extract_wintun_release(archive_path, target_path)

    assert target_path.read_bytes() == b"amd64"


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
