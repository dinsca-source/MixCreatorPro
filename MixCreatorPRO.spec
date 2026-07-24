# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

project_dir = Path(SPECPATH)

customtkinter_data = collect_data_files("customtkinter")

a = Analysis(
    ["main.py"],
    pathex=[str(project_dir)],
    binaries=[
        (str(project_dir / "ffmpeg" / "*.exe"), "ffmpeg"),
    ],
    datas=customtkinter_data,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pydub", "pygame", "mutagen"],
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
    name="MixCreatorPRO",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
