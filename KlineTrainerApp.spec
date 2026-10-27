# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_data_files

yfinance_datas, yfinance_binaries, yfinance_hiddenimports = collect_all('yfinance')
curl_datas, curl_binaries, curl_hiddenimports = collect_all('curl_cffi')


a = Analysis(
    ['webview_app\\main_pywebview.py'],
    pathex=[],
    binaries=yfinance_binaries + curl_binaries,
    datas=[('frontend', 'frontend'), *yfinance_datas, *curl_datas, *collect_data_files('certifi')],
    hiddenimports=sorted(set(yfinance_hiddenimports + curl_hiddenimports + [
        'clr', 'clr_loader', 'pythonnet',
        'webview.platforms.edgechromium', 'webview.platforms.winforms',
    ])),
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
    name='KLineTrainer_US',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon='frontend\\assets\\favicon.ico',
    version='scripts/KLineTrainer_US.version.txt',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='KLineTrainer_US',
)
