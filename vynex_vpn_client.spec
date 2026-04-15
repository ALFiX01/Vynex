# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules


project_dir = Path(globals().get("SPECPATH", Path.cwd())).resolve()
icon_file = project_dir / "icon.ico"

datas = []
datas += collect_data_files("questionary")
datas += collect_data_files("rich")
datas += [("logo.txt", ".")]
for runtime_binary_name in ("amneziawg.exe", "awg.exe"):
    runtime_binary = project_dir / runtime_binary_name
    if runtime_binary.exists():
        datas.append((str(runtime_binary), "."))

hiddenimports = []
hiddenimports += collect_submodules("questionary")
hiddenimports += collect_submodules("rich")


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    console=True,
    icon=str(icon_file) if icon_file.exists() else None,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
