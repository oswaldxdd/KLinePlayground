# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files


a = Analysis(
    ['ai_assistant_tester.py'],
    pathex=['.'],
    binaries=[],
    datas=[('ai_assistant.ico', '.'), ('webview_app/ai_tester_frontend', 'webview_app/ai_tester_frontend'), *collect_data_files('certifi')],
    hiddenimports=['webview.platforms.edgechromium', 'webview.platforms.winforms', 'clr', 'clr_loader', 'pythonnet'],
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
    a.binaries,
    a.datas,
    [],
    name='KLineTrainer_AI_Assistant',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='ai_assistant.ico',
    version='scripts/KLineTrainer_AI_Assistant.version.txt',
)
