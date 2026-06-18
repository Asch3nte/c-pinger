# -*- mode: python ; coding: utf-8 -*-
# Build Linux : pyinstaller claudeping_linux.spec

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('config.yaml.example', '.'),
        ('claudeping/', 'claudeping/'),
    ],
    hiddenimports=[
        'zoneinfo',
        'tzdata',
        'yaml',
        'plyer.platforms.linux.notification',
        'plyer.platforms.linux',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'test', 'distutils'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='claudeping',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
