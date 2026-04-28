"""
Video Gallery API  —  /api/gallery

GET /api/gallery          — all scribe sessions enriched with video / frame info
DELETE /api/gallery/{id}  — delete session + assets (delegates to autocad_routes logic)
"""
import logging
import shutil
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_conn, DATA_DIR

logger = logging.getLogger("app.gallery_routes")
router = APIRouter(prefix="/api/gallery", tags=["gallery"])

# Video base dir — mirrors video_export._BASE
_VIDEO_BASE = DATA_DIR / "videos"


def _video_info(session_id: int) -> dict:
    """Return {has_video, video_type, video_path} for a session."""
    base = _VIDEO_BASE / str(session_id)
    candidates = [
        (f"session_{session_id}_narrated.mp4",   "MP4"),
        (f"session_{session_id}_annotated.mp4",  "MP4"),
        (f"session_{session_id}_annotated.gif",  "GIF"),
        (f"session_{session_id}.mp4",             "MP4"),
        (f"session_{session_id}.gif",             "GIF"),
        (f"session_{session_id}_screenshots.zip", "ZIP"),
    ]
    for filename, vtype in candidates:
        p = base / filename
        if p.exists():
            size_mb = round(p.stat().st_size / 1024 / 1024, 1)
            return {"has_video": True, "video_type": vtype, "video_size_mb": size_mb}
    return {"has_video": False, "video_type": None, "video_size_mb": None}


class GalleryItem(BaseModel):
    id:               int
    title:            str
    target_app:       str
    started_at:       str
    ended_at:         Optional[str]
    status:           str
    screenshot_count: int
    frame_count:      int
    has_video:        bool
    video_type:       Optional[str]
    video_size_mb:    Optional[float]
    narration_text:   Optional[str]


@router.get("", response_model=List[GalleryItem])
def list_gallery(limit: int = 200, offset: int = 0):
    """Return all scribe sessions enriched with frame / video metadata."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            s.id, s.title, s.target_app, s.started_at, s.ended_at,
            s.status, s.narration_text,
            (SELECT COUNT(*) FROM scribe_events e
             WHERE e.session_id = s.id AND e.event_type = 'screenshot') AS screenshot_count,
            (SELECT COUNT(*) FROM frame_annotations fa
             WHERE fa.session_id = s.id) AS frame_count
        FROM scribe_sessions s
        ORDER BY s.started_at DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()

    items = []
    for r in rows:
        info = _video_info(r["id"])
        items.append(GalleryItem(
            id=r["id"],
            title=r["title"] or f"会话 #{r['id']}",
            target_app=r["target_app"],
            started_at=r["started_at"],
            ended_at=r["ended_at"],
            status=r["status"],
            screenshot_count=r["screenshot_count"],
            frame_count=r["frame_count"],
            narration_text=r["narration_text"],
            **info,
        ))
    return items


SESSION_LIMIT = 20


def enforce_session_limit(conn) -> None:
    """Delete oldest sessions when total count exceeds SESSION_LIMIT.

    Call this after inserting a new session (while holding the same connection).
    """
    rows = conn.execute(
        "SELECT id, screenshot_dir FROM scribe_sessions ORDER BY started_at ASC"
    ).fetchall()
    excess = len(rows) - SESSION_LIMIT
    if excess <= 0:
        return
    for row in rows[:excess]:
        folder = Path(row["screenshot_dir"])
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        video_dir = _VIDEO_BASE / str(row["id"])
        if video_dir.exists():
            shutil.rmtree(video_dir, ignore_errors=True)
        conn.execute("DELETE FROM scribe_sessions WHERE id=?", (row["id"],))
        logger.info("Session limit: evicted oldest session #%s", row["id"])
    conn.commit()


@router.delete("/{session_id}")
def delete_gallery_item(session_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT screenshot_dir FROM scribe_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    # Remove screenshot folder
    folder = Path(row["screenshot_dir"])
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)

    # Remove video folder
    video_dir = _VIDEO_BASE / str(session_id)
    if video_dir.exists():
        shutil.rmtree(video_dir, ignore_errors=True)

    conn.execute("DELETE FROM scribe_sessions WHERE id=?", (session_id,))
    conn.commit()
    return {"ok": True}
