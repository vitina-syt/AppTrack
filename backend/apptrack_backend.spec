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
from PyInstaller.utils.hooks import collect_submodules as _collect_submodules

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

import certifi as _certifi

def _portaudio_bins():
    """Find _portaudio.pyd regardless of Python version or pyaudio layout."""
    results = []
    sp_dir = ROOT / 'venv' / 'Lib' / 'site-packages'

    # pyaudio >= 0.2.14: package layout  (site-packages/pyaudio/_portaudio.cpXX.pyd)
    pkg_dir = sp_dir / 'pyaudio'
    if pkg_dir.is_dir():
        for f in pkg_dir.iterdir():
            if f.name.startswith('_portaudio') and f.suffix == '.pyd':
                results.append((str(f), 'pyaudio'))
        return results

    # pyaudio <= 0.2.13: flat layout  (site-packages/_portaudio.cpXX.pyd + pyaudio.py)
    for f in sp_dir.iterdir():
        if f.name.startswith('_portaudio') and f.suffix == '.pyd':
            results.append((str(f), '.'))
    flat_py = sp_dir / 'pyaudio.py'
    if flat_py.exists():
        results.append((str(flat_py), '.'))
    return results

a = Analysis(
    # Entry point: a tiny shim that starts uvicorn programmatically
    [str(ROOT / 'run_server.py')],
    pathex=[str(ROOT)],
    binaries=_pywin32_dlls() + _portaudio_bins(),
    datas=[
        # Include the entire app package
        (str(ROOT / 'app'), 'app'),
        # certifi SSL certificates — required for HTTPS requests (Azure OpenAI)
        (_certifi.where(), 'certifi'),
    ],
    hiddenimports=[
        # ── uvicorn internals ──────────────────────────────────────────────
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
        'uvicorn.config',
        'uvicorn.main',
        # ── FastAPI (sub-modules not auto-detected) ────────────────────────
        'fastapi',
        'fastapi.middleware',
        'fastapi.middleware.cors',
        'fastapi.responses',
        'fastapi.staticfiles',
        'fastapi.routing',
        'fastapi.encoders',
        'fastapi.exceptions',
        'fastapi.params',
        'fastapi.security',
        'fastapi.openapi.utils',
        'fastapi.openapi.models',
        # ── Starlette (FastAPI's foundation) ──────────────────────────────
        'starlette',
        'starlette.middleware',
        'starlette.middleware.cors',
        'starlette.middleware.base',
        'starlette.routing',
        'starlette.responses',
        'starlette.requests',
        'starlette.staticfiles',
        'starlette.background',
        'starlette.concurrency',
        'starlette.datastructures',
        'starlette.exceptions',
        'starlette.formparsers',
        'starlette.types',
        'starlette.websockets',
        'starlette.applications',
        # ── Pydantic ──────────────────────────────────────────────────────
        'pydantic',
        'pydantic.v1',
        'pydantic_core',
        # ── Async ─────────────────────────────────────────────────────────
        'anyio',
        'anyio._backends._asyncio',
        'anyio._backends._trio',
        'sniffio',
        # ── HTTP / multipart ──────────────────────────────────────────────
        'httpx',
        'httpx._transports.default',
        'httpcore',
        'multipart',
        'aiofiles',
        # ── Storage ───────────────────────────────────────────────────────
        'sqlite3',
        # ── Env loading ───────────────────────────────────────────────────
        'dotenv',
        # ── HTTP + SSL (Azure OpenAI 调用) ────────────────────────────────
        'requests',
        'certifi',
        'urllib3',
        'charset_normalizer',
        'idna',
        # ── Pillow — collect all submodules so ImageDraw/ImageFont/etc. all work ─
        *_collect_submodules('PIL'),
        # ── Windows recording libs ────────────────────────────────────────
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
        'win32com',
        'win32com.client',
        'win32com.client.gencache',
        'win32com.client.dynamic',
        'win32com.client.build',
        'win32com.server',
        'win32com.server.util',
        # ── Email (stdlib extension sometimes missed) ─────────────────────
        'email.mime.text',
        'email.mime.multipart',
    ] + (['pyaudio'] if (
        (ROOT / 'venv/Lib/site-packages/pyaudio.py').exists() or     # flat layout
        (ROOT / 'venv/Lib/site-packages/pyaudio').is_dir()           # package layout
    ) else []),
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
