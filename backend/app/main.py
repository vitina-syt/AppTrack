"""
AppTrack backend entry point.
FastAPI + CORS.

In Electron mode the built React frontend is served as static files so the
whole app is accessible at http://127.0.0.1:PORT.  The frontend dist path is
injected via the APPTRACK_FRONTEND_DIST environment variable (set by
electron/main.js).  When that env var is absent the server falls back to
looking for frontend/dist relative to the project root, which covers normal
development via `uvicorn app.main:app --reload`.
"""
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def _load_dotenv():
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)
        return
    except ImportError:
        pass
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


_load_dotenv()

from app.database import init_db
from app.api.routes import editor_routes, gallery_routes, sync_routes

# Recording routes depend on Windows-only packages (pywin32, mss, pynput).
# On a Linux server these imports are skipped gracefully — the server only
# needs gallery / editor / sync functionality.
try:
    from app.api.routes import autocad_routes as _autocad_routes
    from app.api.routes import util_routes    as _util_routes
    _RECORDING_AVAILABLE = True
except Exception as _e:
    _autocad_routes = None   # type: ignore
    _util_routes    = None   # type: ignore
    _RECORDING_AVAILABLE = False
    logging.warning("Recording routes unavailable (running in server mode): %s", _e)

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="AppTrack API",
    description="CAD 操作录屏与教学视频生成",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routers (must be registered BEFORE the SPA catch-all) ─────────────────
if _RECORDING_AVAILABLE:
    app.include_router(_autocad_routes.router)
    app.include_router(_util_routes.router)

app.include_router(editor_routes.router)
app.include_router(gallery_routes.router)
app.include_router(sync_routes.router)


# ── Frontend static file serving (SPA) ───────────────────────────────────────
# Resolve the frontend dist directory.
# Priority: APPTRACK_FRONTEND_DIST env var → project-relative fallback.
_env_dist = os.environ.get("APPTRACK_FRONTEND_DIST", "")
if _env_dist:
    _FRONTEND_DIST = Path(_env_dist)
else:
    # Project root is three levels up from backend/app/main.py
    _FRONTEND_DIST = Path(__file__).parent.parent.parent / "frontend" / "dist"

if _FRONTEND_DIST.exists():
    # Mount the /assets sub-directory (JS/CSS chunks) as a static file mount
    # so they are served with correct Content-Type and caching headers.
    _assets_dir = _FRONTEND_DIST / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str):
        """
        Catch-all: serve existing static files (e.g. favicon.ico),
        fall back to index.html for all SPA routes (including root "/").
        """
        # full_path is "" for GET /  — skip is_file check (it's a dir)
        if full_path:
            candidate = _FRONTEND_DIST / full_path
            if candidate.is_file():
                return FileResponse(str(candidate))
        return FileResponse(str(_FRONTEND_DIST / "index.html"))
