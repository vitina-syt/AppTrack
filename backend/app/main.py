"""
AppTrack backend entry point.
FastAPI + CORS + lifespan: tracker starts automatically on launch.
"""
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load .env from backend/ directory (if it exists) before anything else.
# Supports both python-dotenv and a minimal fallback parser.
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
    # Minimal fallback: parse KEY=VALUE lines without the dotenv package
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
from app.tracker import tracker
from app.api.routes import tracker_routes, sessions, stats, recordings, scribe_routes, autocad_routes

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    tracker.start(poll_interval=5)
    yield
    tracker.stop()


app = FastAPI(
    title="AppTrack API",
    description="Windows 桌面软件使用时长追踪 — 本地优先",
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

app.include_router(tracker_routes.router)
app.include_router(sessions.router)
app.include_router(stats.router)
app.include_router(recordings.router)
app.include_router(scribe_routes.router)
app.include_router(autocad_routes.router)


@app.get("/")
def root():
    return {"app": "AppTrack", "docs": "/docs"}
