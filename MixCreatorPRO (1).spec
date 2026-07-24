# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files

project_dir = SPECPATH

customtkinter_data = collect_data_files("customtkinter")

a = Analysis(
    ["main.py"],
    pathex=[project_dir],
    binaries=[
        (str(project_dir / "ffmpeg" / "ffmpeg.exe"), "ffmpeg"),
        (str(project_dir / "ffmpeg" / "ffprobe.exe"), "ffmpeg"),
        (str(project_dir / "ffmpeg" / "ffplay.exe"), "ffmpeg"),
    ],
    datas=customtkinter_data,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pydub",
        "pygame",
        "mutagen",
    ],
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
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
