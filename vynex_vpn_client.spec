# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


project_dir = Path(globals().get("SPECPATH", Path.cwd())).resolve()
icon_file = project_dir / "icon.ico"

def _existing_project_data(*names):
    result = []
    for name in names:
        source = project_dir / name
        if source.exists():
            result.append((str(source), "."))
    return result


def _collect_pyside6_qt_plugins(*plugin_groups):
    try:
        import PySide6
    except Exception:
        return []

    pyside_dir = Path(PySide6.__file__).resolve().parent
    result = []
    for plugin_root, target_root in (
        (pyside_dir / "plugins", Path("PySide6") / "plugins"),
        (pyside_dir / "Qt" / "plugins", Path("PySide6") / "Qt" / "plugins"),
    ):
        for group in plugin_groups:
            group_dir = plugin_root / group
            if not group_dir.exists():
                continue
            for plugin in group_dir.glob("*.dll"):
                result.append((str(plugin), str(target_root / group)))
    return result


datas = []
datas += _existing_project_data("logo.txt", "icon.ico")

binaries = []
binaries += _collect_pyside6_qt_plugins("platforms", "styles", "imageformats", "iconengines")
for runtime_binary_name in ("amneziawg.exe", "awg.exe"):
    runtime_binary = project_dir / runtime_binary_name
    if runtime_binary.exists():
        binaries.append((str(runtime_binary), "."))

hiddenimports = []
hiddenimports += [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
]
hiddenimports += collect_submodules("vynex_vpn_client.gui")


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["questionary", "rich", "httpx._main", "click"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VynexVPNClient",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon=str(icon_file) if icon_file.exists() else None,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
