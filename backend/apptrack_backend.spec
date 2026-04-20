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

import site, os as _os

# Collect pywin32 DLLs — PyInstaller often misses them
def _pywin32_dlls():
    results = []
    for sp in site.getsitepackages():
        win32_dir = _os.path.join(sp, 'pywin32_system32')
        if _os.path.isdir(win32_dir):
            for fname in _os.listdir(win32_dir):
                if fname.endswith('.dll'):
                    results.append((_os.path.join(win32_dir, fname), '.'))
    return results

a = Analysis(
    # Entry point: a tiny shim that starts uvicorn programmatically
    [str(ROOT / 'run_server.py')],
    pathex=[str(ROOT)],
    binaries=_pywin32_dlls(),
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
        'email.mime.text',
        'email.mime.multipart',
        'multipart',
        'starlette',
        'starlette.middleware',
        'starlette.middleware.cors',
        'anyio',
        'anyio._backends._asyncio',
        'anyio._backends._trio',
        'sniffio',
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
