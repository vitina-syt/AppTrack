"""
Frame Editor API  —  /api/autocad/sessions/{id}/frames/*

GET    /api/autocad/sessions/{id}/frames              — list all screenshot frames + annotations
PATCH  /api/autocad/sessions/{id}/frames/{event_id}   — save title / narration / shapes for one frame
POST   /api/autocad/sessions/{id}/frames/distribute   — split session narration into per-frame chunks
POST   /api/autocad/sessions/{id}/video/annotated     — generate video with annotations burned in

Annotation shape JSON format (stored in shapes_json column):
  {"id": int, "type": "circle", "cx": 0-1, "cy": 0-1, "rx": 0-1, "ry": 0-1,
   "label": str, "color": str}
  {"id": int, "type": "blur",   "x": 0-1, "y": 0-1, "w": 0-1, "h": 0-1}
  {"id": int, "type": "text",   "x": 0-1, "y": 0-1, "text": str,
   "color": str, "size": int}
All coordinates are relative to image dimensions (0–1).
"""
import json
import re
import threading
import logging
from typing import Optional
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.database import get_conn

logger = logging.getLogger("app.editor_routes")

router = APIRouter(prefix="/api/autocad", tags=["autocad-editor"])


# ── models ────────────────────────────────────────────────────────────────────

class FrameUpdate(BaseModel):
    title:       Optional[str] = None
    narration:   Optional[str] = None
    shapes_json: Optional[str] = None   # JSON string


# ── list frames ───────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/frames")
def list_frames(session_id: int):
    """Return all screenshot frames for a session with their annotations."""
    conn = get_conn()
    if not conn.execute(
        "SELECT id FROM scribe_sessions WHERE id=? AND target_app='acad.exe'",
        (session_id,),
    ).fetchone():
        raise HTTPException(status_code=404, detail="AutoCAD session not found")

    rows = conn.execute(
        """SELECT e.id          AS event_id,
                  e.seq,
                  e.screenshot_path,
                  s.screenshot_dir,
                  COALESCE(fa.title,       '')   AS title,
                  COALESCE(fa.narration,   '')   AS narration,
                  COALESCE(fa.shapes_json, '[]') AS shapes_json
           FROM   scribe_events  e
           JOIN   scribe_sessions s  ON s.id = e.session_id
           LEFT JOIN frame_annotations fa
                  ON fa.event_id = e.id AND fa.session_id = ?
           WHERE  e.session_id = ? AND e.event_type = 'screenshot'
           ORDER BY e.seq""",
        (session_id, session_id),
    ).fetchall()
    return [dict(r) for r in rows]


# ── update one frame ──────────────────────────────────────────────────────────

@router.patch("/sessions/{session_id}/frames/{event_id}")
def update_frame(session_id: int, event_id: int, body: FrameUpdate):
    """Upsert title / narration / shapes for one frame."""
    conn = get_conn()
    # Validate event belongs to session
    row = conn.execute(
        "SELECT seq FROM scribe_events WHERE id=? AND session_id=?",
        (event_id, session_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Frame not found")

    conn.execute(
        """INSERT INTO frame_annotations
               (session_id, event_id, seq, title, narration, shapes_json)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(session_id, event_id) DO UPDATE SET
               title       = CASE WHEN excluded.title       != '' THEN excluded.title       ELSE title       END,
               narration   = CASE WHEN excluded.narration   != '' THEN excluded.narration   ELSE narration   END,
               shapes_json = excluded.shapes_json""",
        (
            session_id,
            event_id,
            row["seq"],
            body.title     or "",
            body.narration or "",
            body.shapes_json if body.shapes_json is not None else "[]",
        ),
    )
    conn.commit()
    return {"ok": True}


# ── distribute narration ──────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/frames/distribute")
def distribute_narration(session_id: int):
    """
    Split the session's full narration_text into per-frame chunks and
    write them into frame_annotations.  Existing narration text is replaced.
    """
    conn = get_conn()
    sess = conn.execute(
        "SELECT narration_text FROM scribe_sessions WHERE id=? AND target_app='acad.exe'",
        (session_id,),
    ).fetchone()
    if not sess:
        raise HTTPException(status_code=404, detail="AutoCAD session not found")
    if not sess["narration_text"]:
        raise HTTPException(status_code=422, detail="No narration text — generate narration first")

    frames = conn.execute(
        """SELECT e.id AS event_id, e.seq
           FROM scribe_events e
           WHERE e.session_id = ? AND e.event_type = 'screenshot'
           ORDER BY e.seq""",
        (session_id,),
    ).fetchall()
    if not frames:
        raise HTTPException(status_code=422, detail="No screenshot frames found")

    # Split narration into sentences (handles both Chinese and English)
    text      = sess["narration_text"].strip()
    sentences = [s.strip() for s in re.split(r"(?<=[。！？.!?\n])\s*", text) if s.strip()]
    if not sentences:
        sentences = [text]

    n_frames = len(frames)
    n_sents  = len(sentences)

    for i, frame in enumerate(frames):
        # Distribute sentences proportionally
        if n_sents == 0:
            chunk = ""
        elif n_frames <= n_sents:
            start = int(i * n_sents / n_frames)
            end   = int((i + 1) * n_sents / n_frames)
            chunk = " ".join(sentences[start:end])
        else:
            idx   = int(i * n_sents / n_frames)
            chunk = sentences[min(idx, n_sents - 1)]

        conn.execute(
            """INSERT INTO frame_annotations
                   (session_id, event_id, seq, title, narration, shapes_json)
               VALUES (?, ?, ?, ?, ?, '[]')
               ON CONFLICT(session_id, event_id) DO UPDATE SET
                   title     = excluded.title,
                   narration = excluded.narration""",
            (session_id, frame["event_id"], frame["seq"], f"步骤 {i + 1}", chunk),
        )

    conn.commit()
    return {"ok": True, "frames_updated": n_frames}


# ── annotated video ───────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/video/annotated")
def generate_annotated_video(
    session_id: int,
    fps: float = Query(default=1.0, ge=0.1, le=10.0),
):
    """
    Generate a video with annotation shapes (circles, blur, text) burned into
    every frame.  Runs in background; poll /video/status to check progress.
    """
    conn = get_conn()
    if not conn.execute(
        "SELECT id FROM scribe_sessions WHERE id=? AND target_app='acad.exe'",
        (session_id,),
    ).fetchone():
        raise HTTPException(status_code=404, detail="AutoCAD session not found")

    def _do():
        try:
            from app.video_export import build_annotated_video
            build_annotated_video(session_id, fps=fps)
        except Exception as exc:
            logger.error("Annotated video generation failed for session %d: %s", session_id, exc)

    threading.Thread(target=_do, daemon=True).start()
    return {"ok": True, "session_id": session_id, "status": "generating"}