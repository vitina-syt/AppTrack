"""
AppTrack backend entry point.
FastAPI + CORS.
"""
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


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
from app.api.routes import autocad_routes, editor_routes, gallery_routes, util_routes

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

app.include_router(autocad_routes.router)
app.include_router(editor_routes.router)
app.include_router(gallery_routes.router)
app.include_router(util_routes.router)


@app.get("/")
def root():
    return {"app": "AppTrack", "docs": "/docs"}
