# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['d:\\lib\\CODEGAMES\\VIVY\\desktop_pet_dual_platform.py'],
    pathex=[],
    binaries=[],
    datas=[('d:\\lib\\CODEGAMES\\VIVY\\static\\images', 'static\\images'), ('d:\\lib\\CODEGAMES\\VIVY\\env.example', '.')],
    hiddenimports=[],
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
    [],
    exclude_binaries=True,
    name='VIVY',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['d:\\lib\\CODEGAMES\\VIVY\\release\\vivy.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='VIVY',
)
