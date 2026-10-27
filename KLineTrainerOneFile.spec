# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_data_files

yfinance_datas, yfinance_binaries, yfinance_hiddenimports = collect_all('yfinance')
curl_datas, curl_binaries, curl_hiddenimports = collect_all('curl_cffi')
hiddenimports = sorted(set(yfinance_hiddenimports + curl_hiddenimports + [
    'clr', 'clr_loader', 'pythonnet',
    'webview.platforms.edgechromium', 'webview.platforms.winforms',
]))

a = Analysis(
    ['webview_app/main_pywebview.py'],
    pathex=['.'],
    binaries=yfinance_binaries + curl_binaries,
    datas=[('frontend', 'frontend'), *yfinance_datas, *curl_datas, *collect_data_files('certifi')],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='KLineTrainer_US',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon='frontend\\assets\\favicon.ico',
    version='scripts/KLineTrainer_US.version.txt',
)
