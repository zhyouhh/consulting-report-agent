# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

root = Path.cwd()
sys.path.insert(0, str(root))

from build_support import (
    require_non_empty_bundle_text_file,
    validate_bundle_managed_search_pool,
)

block_cipher = None
datas = [
    ('skill', 'skill'),
    ('frontend/dist', 'frontend/dist'),
]
managed_client_token_file = require_non_empty_bundle_text_file(
    root,
    'managed_client_token.txt',
)
datas.append((str(managed_client_token_file), '.'))
managed_search_pool_file = validate_bundle_managed_search_pool(
    root,
    'managed_search_pool.json',
)
datas.append((str(managed_search_pool_file), '.'))

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'webview.platforms.winforms',
        'webview.platforms.edgechromium',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'webview.platforms.qt',
        'webview.platforms.gtk',
        'webview.platforms.cocoa',
        'webview.platforms.android',
        'webview.platforms.cef',
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
        'qtpy',
        'gi',
    ],
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
    exclude_binaries=True,
    name='咨询报告助手',
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
    icon='app_icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='咨询报告助手',
)
