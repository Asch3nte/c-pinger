# -*- mode: python ; coding: utf-8 -*-
# Build Windows (à exécuter sur Windows) :
#   pip install pyinstaller pyyaml plyer PySide6 tzdata
#   pyinstaller claudeping_windows.spec

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
        'plyer.platforms.win.notification',
        'plyer.platforms.win',
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
    name='ClaudePing',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,   # Pas de fenêtre console sur Windows
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
