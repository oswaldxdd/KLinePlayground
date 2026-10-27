# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

yfinance_datas, yfinance_binaries, yfinance_hiddenimports = collect_all('yfinance')

block_cipher = None

a = Analysis(
    ['webview_app/main_pywebview.py'],
    pathex=['.'],
    binaries=yfinance_binaries,
    datas=[('frontend', 'frontend'), *yfinance_datas],
    hiddenimports=yfinance_hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    a.binaries,
    a.zipfiles,
    a.datas,
    name='KlineTrainerApp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon='frontend\\assets\\favicon.ico'
)
