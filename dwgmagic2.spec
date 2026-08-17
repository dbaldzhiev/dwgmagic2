# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build: one onedir bundle containing both executables.

dwgmagic2.exe  — console build, used for --cli and for debugging a bad launch.
dwgmagic2w.exe — windowed build, what the shortcut and context menu open.

Onedir rather than onefile: onefile re-extracts to TEMP on every launch, and a
blocked extraction fails the same silent way this rewrite exists to eliminate.
"""
from PyInstaller.utils.hooks import collect_data_files

# build_environment() resolves templates relative to dwgmagic.__file__, so they
# must keep their package-relative location inside the bundle.
datas = [("dwgmagic/templates", "dwgmagic/templates")]
datas += collect_data_files("customtkinter")  # bundled theme JSON + assets
datas += collect_data_files("tkinterdnd2")  # native tkdnd binaries

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=["tkinterdnd2"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "setuptools"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe_console = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="dwgmagic2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon="magic.ico",
)

exe_windowed = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="dwgmagic2w",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="magic.ico",
)

coll = COLLECT(
    exe_console,
    exe_windowed,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="dwgmagic2",
)
