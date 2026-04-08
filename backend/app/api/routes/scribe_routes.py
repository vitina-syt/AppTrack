"""
CreoScribe REST API

POST   /api/scribe/start                       — start a scribe session
POST   /api/scribe/stop                        — stop & generate narration
GET    /api/scribe/status                      — active session status
GET    /api/scribe/sessions                    — list all sessions
GET    /api/scribe/sessions/{id}               — session detail
PATCH  /api/scribe/sessions/{id}               — edit title / narration
DELETE /api/scribe/sessions/{id}               — delete session + data
GET    /api/scribe/sessions/{id}/events        — event timeline
POST   /api/scribe/sessions/{id}/generate      — (re)generate narration via Claude
POST   /api/scribe/sessions/{id}/avatar        — submit to HeyGen / D-ID
GET    /api/scribe/sessions/{id}/avatar/status — poll avatar job status
GET    /api/scribe/sessions/{id}/events/{eid}/image — serve screenshot
"""
import shutil
import threading
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse

from app.database import get_conn
from app.scribe_agent import scribe_agent, _generate_narration_sync
from app.models import (
    ScribeSession, ScribeSessionUpdate,
    ScribeEvent, ScribeAgentStatus,
)

router = APIRouter(prefix="/api/scribe", tags=["scribe"])


# ── Session control ───────────────────────────────────────────────────────────

@router.post("/start", response_model=ScribeAgentStatus)
def start_scribe(
    title:               str  = Query(default="",         description="Session title"),
    target_app:          str  = Query(default="xtop.exe", description="Process name to monitor"),
    screenshot_interval: int  = Query(default=30, ge=5, le=300),
    enable_voice:        bool = Query(default=True),
    enable_uia:          bool = Query(default=True),
):
    try:
        scribe_agent.start(
            title=title,
            target_app=target_app,
            screenshot_interval=screenshot_interval,
            enable_voice=enable_voice,
            enable_uia=enable_uia,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return scribe_agent.status


@router.post("/stop", response_model=ScribeAgentStatus)
def stop_scribe(
    generate: bool = Query(default=True, description="Generate narration after stopping"),
    background_tasks: BackgroundTasks = None,
):
    status = scribe_agent.status
    if not status["running"]:
        raise HTTPException(status_code=409, detail="No active scribe session")

    sid = status["session_id"]

    # Stop capture immediately, run narration in background if requested
    if generate:
        # Stop without blocking narration (run it in a thread)
        def _stop_and_narrate():
            scribe_agent.stop(generate_narration=True)

        t = threading.Thread(target=_stop_and_narrate, daemon=True)
        t.start()
    else:
        scribe_agent.stop(generate_narration=False)

    return {
        "running": False,
        "session_id": sid,
        "events_captured": status["events_captured"],
        "voice_segments": status["voice_segments"],
        "uia_events": status["uia_events"],
    }


@router.get("/status", response_model=ScribeAgentStatus)
def get_scribe_status():
    return scribe_agent.status


# ── Sessions CRUD ─────────────────────────────────────────────────────────────

@router.get("/sessions", response_model=List[ScribeSession])
def list_sessions(
    limit:  int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0,  ge=0),
):
    conn = get_conn()
    rows = conn.execute(
        """SELECT s.*,
                  (SELECT COUNT(*) FROM scribe_events e WHERE e.session_id = s.id) AS event_count
           FROM scribe_sessions s
           ORDER BY s.started_at DESC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/sessions/{session_id}", response_model=ScribeSession)
def get_session(session_id: int):
    conn = get_conn()
    row = conn.execute(
        """SELECT s.*,
                  (SELECT COUNT(*) FROM scribe_events e WHERE e.session_id = s.id) AS event_count
           FROM scribe_sessions s WHERE s.id = ?""",
        (session_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return dict(row)


@router.patch("/sessions/{session_id}", response_model=ScribeSession)
def update_session(session_id: int, body: ScribeSessionUpdate):
    conn = get_conn()
    if not conn.execute("SELECT id FROM scribe_sessions WHERE id=?", (session_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Session not found")

    if body.title is not None:
        conn.execute("UPDATE scribe_sessions SET title=? WHERE id=?", (body.title, session_id))
    if body.narration_text is not None:
        conn.execute(
            "UPDATE scribe_sessions SET narration_text=? WHERE id=?",
            (body.narration_text, session_id),
        )
    conn.commit()
    return get_session(session_id)


@router.delete("/sessions/{session_id}")
def delete_session(session_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT screenshot_dir FROM scribe_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    folder = Path(row["screenshot_dir"])
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)

    conn.execute("DELETE FROM scribe_sessions WHERE id=?", (session_id,))
    conn.commit()
    return {"ok": True}


# ── Events ────────────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/events", response_model=List[ScribeEvent])
def list_events(
    session_id: int,
    event_type: Optional[str] = Query(default=None),
    limit:  int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0,   ge=0),
):
    conn = get_conn()
    if not conn.execute("SELECT id FROM scribe_sessions WHERE id=?", (session_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Session not found")

    if event_type:
        rows = conn.execute(
            "SELECT * FROM scribe_events WHERE session_id=? AND event_type=? ORDER BY seq LIMIT ? OFFSET ?",
            (session_id, event_type, limit, offset),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM scribe_events WHERE session_id=? ORDER BY seq LIMIT ? OFFSET ?",
            (session_id, limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/sessions/{session_id}/events/{event_id}/image")
def get_event_image(session_id: int, event_id: int):
    conn = get_conn()
    row = conn.execute(
        """SELECT e.screenshot_path, s.screenshot_dir
           FROM scribe_events e
           JOIN scribe_sessions s ON s.id = e.session_id
           WHERE e.id=? AND e.session_id=? AND e.event_type='screenshot'""",
        (event_id, session_id),
    ).fetchone()
    if not row or not row["screenshot_path"]:
        raise HTTPException(status_code=404, detail="No screenshot for this event")

    path = Path(row["screenshot_dir"]) / row["screenshot_path"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Screenshot file not found")

    media_type = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return FileResponse(str(path), media_type=media_type)


# ── Narration generation ──────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/generate")
def regenerate_narration(session_id: int):
    """(Re)generate the narration text for an existing session using Claude."""
    conn = get_conn()
    sess = conn.execute(
        "SELECT id, target_app FROM scribe_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    target_app = sess["target_app"]

    rows = conn.execute(
        """SELECT event_type, timestamp, app_name, window_title,
                  uia_element_name, uia_element_type, uia_automation_id,
                  screenshot_path, voice_text, voice_confidence
           FROM scribe_events WHERE session_id=? ORDER BY seq""",
        (session_id,),
    ).fetchall()
    events = [dict(r) for r in rows]

    # Mark as processing
    conn.execute(
        "UPDATE scribe_sessions SET status='processing', error_message=NULL WHERE id=?",
        (session_id,),
    )
    conn.commit()

    def _do_generate():
        narration = _generate_narration_sync(events, target_app)
        from app.database import DB_PATH
        import sqlite3
        c = sqlite3.connect(str(DB_PATH))
        c.execute(
            "UPDATE scribe_sessions SET narration_text=?, status='done' WHERE id=?",
            (narration, session_id),
        )
        c.commit()
        c.close()

    t = threading.Thread(target=_do_generate, daemon=True)
    t.start()

    return {"ok": True, "session_id": session_id, "status": "processing"}


# ── Avatar export ─────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/avatar")
def submit_avatar(
    session_id: int,
    provider:   str = Query(default="heygen", description="heygen or did"),
    avatar_id:  str = Query(default="", description="Provider avatar ID"),
    voice_id:   str = Query(default="", description="Provider voice ID"),
    api_key:    str = Query(default="", description="API key (or set env var)"),
):
    """Submit session narration to HeyGen / D-ID for avatar video generation."""
    conn = get_conn()
    row = conn.execute(
        "SELECT narration_text, status FROM scribe_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if not row["narration_text"]:
        raise HTTPException(status_code=422, detail="No narration text — run /generate first")

    try:
        from app.avatar_export import export_avatar
        result = export_avatar(
            narration_text=row["narration_text"],
            provider=provider,
            avatar_id=avatar_id or "",
            voice_id=voice_id   or "",
            api_key=api_key     or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # Store job_id in DB
    conn.execute(
        "UPDATE scribe_sessions SET avatar_job_id=? WHERE id=?",
        (result.get("job_id"), session_id),
    )
    conn.commit()
    return result


@router.get("/sessions/{session_id}/avatar/status")
def poll_avatar(
    session_id: int,
    provider: str = Query(default="heygen"),
    api_key:  str = Query(default=""),
):
    """Poll the avatar generation job for completion."""
    conn = get_conn()
    row = conn.execute(
        "SELECT avatar_job_id FROM scribe_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if not row["avatar_job_id"]:
        raise HTTPException(status_code=422, detail="No avatar job submitted yet")

    try:
        from app.avatar_export import poll_avatar_job
        result = poll_avatar_job(
            job_id=row["avatar_job_id"],
            provider=provider,
            api_key=api_key or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # If done, persist the video URL
    if result.get("status") == "done" and result.get("video_url"):
        conn.execute(
            "UPDATE scribe_sessions SET avatar_video_url=? WHERE id=?",
            (result["video_url"], session_id),
        )
        conn.commit()

    return result