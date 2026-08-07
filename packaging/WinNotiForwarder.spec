# -*- mode: python ; coding: utf-8 -*-
# Build with: pyinstaller packaging/WinNotiForwarder.spec
#
# Onefile build so the running exe's own path (not a temp extraction dir) is
# what receives package identity - that's the path you pass as
# -ExternalLocation to Add-AppxPackage in register_app.ps1.

from pathlib import Path

project_root = Path(SPECPATH).parent

a = Analysis(
    [str(project_root / 'main.py')],
    pathex=[str(project_root)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='WinNotiForwarder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    manifest=str(project_root / 'packaging' / 'app.manifest'),
)
