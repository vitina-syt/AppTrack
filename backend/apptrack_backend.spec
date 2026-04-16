# PyInstaller spec — bundles the Python backend into a single folder.
# The resulting `dist/apptrack_backend/` directory is what electron-builder
# packages into the installer (see package.json extraResources).
#
# Usage (run from the backend/ directory):
#   pip install pyinstaller
#   pyinstaller apptrack_backend.spec
#
# Output: backend/dist/apptrack_backend/apptrack_backend.exe

import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH)   # backend/

a = Analysis(
    # Entry point: a tiny shim that starts uvicorn programmatically
    [str(ROOT / 'run_server.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Include the entire app package
        (str(ROOT / 'app'), 'app'),
    ],
    hiddenimports=[
        'uvicorn',
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
        'fastapi',
        'pydantic',
        'sqlite3',
        'anthropic',
        'PIL',
        'PIL.Image',
        'mss',
        'pynput',
        'pynput.mouse',
        'pynput.keyboard',
        'psutil',
        'win32gui',
        'win32process',
        'win32con',
        'pythoncom',
        'pywintypes',
        'aiofiles',
        'httpx',
        'dotenv',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'test', 'unittest'],
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
    name='apptrack_backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,   # keep True so Electron can read stdout/stderr
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='apptrack_backend',
)
