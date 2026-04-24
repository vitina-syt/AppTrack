"""
AutoCAD Scribe REST API  —  /api/autocad/*

POST   /api/autocad/start                        — start scribe session (any target app)
POST   /api/autocad/stop                         — stop & generate narration
GET    /api/autocad/status                       — live agent status
GET    /api/autocad/sessions                     — list all sessions
GET    /api/autocad/sessions/{id}                — session detail
PATCH  /api/autocad/sessions/{id}                — edit title / narration
DELETE /api/autocad/sessions/{id}                — delete session + screenshots
GET    /api/autocad/sessions/{id}/events         — event timeline (filterable)
POST   /api/autocad/sessions/{id}/generate       — re-generate narration
POST   /api/autocad/sessions/{id}/video          — generate local slideshow video
GET    /api/autocad/sessions/{id}/video/download — download generated video
POST   /api/autocad/sessions/{id}/avatar         — export to HeyGen / D-ID
GET    /api/autocad/sessions/{id}/avatar/status  — poll avatar job
GET    /api/autocad/sessions/{id}/events/{eid}/image — serve screenshot
GET    /api/autocad/commands                     — list known AutoCAD command categories
"""
import logging
import shutil
import threading
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger("app.autocad_routes")

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.database import get_conn
from app.autocad_agent import (
    autocad_agent, is_target_running, get_running_windows,
    _generate_narration_sync, _CATEGORY_LABELS,
)
from app.autocad_monitor import COMMAND_CATEGORIES
from app.models import ScribeSession, ScribeSessionUpdate, ScribeEvent, ScribeAgentStatus

router = APIRouter(prefix="/api/autocad", tags=["autocad"])


# ── Agent control ─────────────────────────────────────────────────────────────

@router.get("/running-windows")
def list_running_windows():
    """Return visible, titled windows as [{exe, title, pid}] — used to populate the app selector."""
    return get_running_windows()


@router.post("/start", response_model=ScribeAgentStatus)
def start_autocad(
    title:                  str  = Query(default=""),
    target_exe:             str  = Query(default="acad.exe"),
    screenshot_interval:    int  = Query(default=30, ge=5, le=300),
    enable_voice:           bool = Query(default=True),
    enable_com:             bool = Query(default=True),
    screenshot_on_command:  bool = Query(default=True),
    screenshot_on_click:        bool = Query(default=False),
    screenshot_on_middle_drag:  bool = Query(default=False, description="Creo: middle-button drag → rotation"),
    screenshot_on_scroll_zoom:  bool = Query(default=False, description="Creo: scroll wheel zoom in/out"),
    screenshot_on_shift_pan:    bool = Query(default=False, description="Creo: Shift+middle-button drag → pan"),
    creo_trail_file:            str  = Query(default="", description="Path to Creo trail.txt (optional, overrides auto-detection)"),
    background:                 str  = Query(default="", description="Background context for AI narration generation"),
):
    if not is_target_running(target_exe):
        raise HTTPException(
            status_code=422,
            detail=f"{target_exe} 未运行，请先启动该程序再开始监听。",
        )
    try:
        autocad_agent.start(
            title=title,
            target_exe=target_exe,
            screenshot_interval=screenshot_interval,
            enable_voice=enable_voice,
            enable_com=enable_com,
            screenshot_on_command=screenshot_on_command,
            screenshot_on_click=screenshot_on_click,
            screenshot_on_middle_drag=screenshot_on_middle_drag,
            screenshot_on_scroll_zoom=screenshot_on_scroll_zoom,
            screenshot_on_shift_pan=screenshot_on_shift_pan,
            creo_trail_file=creo_trail_file,
            background=background,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return autocad_agent.status


@router.post("/stop", response_model=ScribeAgentStatus)
def stop_autocad():
    status = autocad_agent.status
    if not status["running"]:
        return {
            "running":         False,
            "session_id":      status.get("session_id"),
            "events_captured": status.get("events_captured", 0),
            "voice_segments":  status.get("voice_segments", 0),
            "uia_events":      status.get("uia_events", 0),
        }

    sid = status["session_id"]

    threading.Thread(target=autocad_agent.stop, daemon=True).start()

    return {
        "running":         False,
        "session_id":      sid,
        "events_captured": status["events_captured"],
        "voice_segments":  status["voice_segments"],
        "uia_events":      status["uia_events"],
    }


@router.get("/status", response_model=ScribeAgentStatus)
def get_autocad_status():
    return autocad_agent.status


# ── Sessions ──────────────────────────────────────────────────────────────────

@router.get("/sessions", response_model=List[ScribeSession])
def list_autocad_sessions(
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
def get_autocad_session(session_id: int):
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
def update_autocad_session(session_id: int, body: ScribeSessionUpdate):
    conn = get_conn()
    if not conn.execute(
        "SELECT id FROM scribe_sessions WHERE id=?",
        (session_id,),
    ).fetchone():
        raise HTTPException(status_code=404, detail="Session not found")

    if body.title is not None:
        conn.execute("UPDATE scribe_sessions SET title=? WHERE id=?", (body.title, session_id))
    if body.narration_text is not None:
        conn.execute(
            "UPDATE scribe_sessions SET narration_text=? WHERE id=?",
            (body.narration_text, session_id),
        )
    conn.commit()
    return get_autocad_session(session_id)


@router.delete("/sessions/{session_id}")
def delete_autocad_session(session_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT screenshot_dir FROM scribe_sessions WHERE id=?",
        (session_id,),
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
def list_autocad_events(
    session_id: int,
    event_type: Optional[str] = Query(default=None),
    category:   Optional[str] = Query(default=None, description="Filter by CAD category: draw|edit|3d|annotate|view|layer|block|file"),
    limit:  int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0,   ge=0),
):
    conn = get_conn()
    if not conn.execute(
        "SELECT id FROM scribe_sessions WHERE id=?",
        (session_id,),
    ).fetchone():
        raise HTTPException(status_code=404, detail="Session not found")

    clauses = ["session_id=?"]
    params: list = [session_id]

    if event_type:
        clauses.append("event_type=?")
        params.append(event_type)

    if category:
        clauses.append("uia_element_type LIKE ?")
        params.append(f"%acad_cmd:{category}%")

    where = " AND ".join(clauses)
    rows = conn.execute(
        f"SELECT * FROM scribe_events WHERE {where} ORDER BY seq LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return [dict(r) for r in rows]


# ── Local video generation ────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/video")
def generate_video(
    session_id: int,
    fps: float = Query(default=1.0, ge=0.1, le=10.0,
                       description="Frames per second (default 1 = 1 screenshot/sec)"),
):
    """
    Generate a slideshow video from this session's screenshots.
    Uses ffmpeg (MP4) if available, otherwise Pillow (GIF).
    Returns immediately with a background task; poll /video/download to check.
    """
    conn = get_conn()
    if not conn.execute(
        "SELECT id FROM scribe_sessions WHERE id=?",
        (session_id,),
    ).fetchone():
        raise HTTPException(status_code=404, detail="Session not found")

    def _do():
        try:
            from app.video_export import build_video
            build_video(session_id, fps=fps)
        except Exception as exc:
            logger.error("Video generation failed for session %d: %s", session_id, exc)

    threading.Thread(target=_do, daemon=True).start()
    return {"ok": True, "session_id": session_id, "status": "generating"}


@router.get("/sessions/{session_id}/video/status")
def video_status(session_id: int):
    """
    Diagnostic endpoint — returns generation status + environment info.
    Frontend polls this instead of using HEAD on /download.
    """
    import shutil as _shutil
    from app.video_export import get_job_state, get_existing_video, _screenshot_paths, _PIL
    from app.autocad_agent import _MSS, SCREENSHOTS_BASE
    from app.database import DATA_DIR

    # Screenshot count (files that exist on disk)
    shots = _screenshot_paths(session_id)

    # Also count raw DB rows (regardless of file existence) for diagnostics
    conn2 = get_conn()
    db_shot_count = conn2.execute(
        "SELECT COUNT(*) FROM scribe_events WHERE session_id=? AND event_type='screenshot'",
        (session_id,),
    ).fetchone()[0]

    # Existing file
    existing = get_existing_video(session_id)

    # Job state
    state = get_job_state(session_id)
    # If file exists but state wasn't tracked (e.g. after server restart), mark ready
    if existing and state["status"] == "not_started":
        state = {"status": "ready", "error": None}

    # Test actual write permission on the data directory
    _data_dir_writable = False
    _data_dir_error = None
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _probe = DATA_DIR / ".write_probe"
        _probe.write_bytes(b"1")
        _probe.unlink()
        _data_dir_writable = True
    except Exception as e:
        _data_dir_error = str(e)

    return {
        "status":              state["status"],       # not_started|generating|ready|error
        "error":               state["error"],
        "screenshot_count":    len(shots),            # files that exist on disk
        "db_screenshot_count": db_shot_count,         # rows in DB (may differ if files missing)
        "has_file":            existing is not None,
        "file_type":           existing[1] if existing else None,
        "screenshots_dir":     str(SCREENSHOTS_BASE / str(session_id)),
        "data_dir":            str(DATA_DIR),
        "data_dir_writable":   _data_dir_writable,
        "data_dir_error":      _data_dir_error,
        "env": {
            "ffmpeg": _shutil.which("ffmpeg") is not None,
            "pillow": _PIL,
            "mss":    _MSS,
        },
    }


@router.get("/sessions/{session_id}/video/download")
def download_video(session_id: int):
    """Download the generated video file (MP4 / GIF / ZIP)."""
    conn = get_conn()
    if not conn.execute(
        "SELECT id FROM scribe_sessions WHERE id=?",
        (session_id,),
    ).fetchone():
        raise HTTPException(status_code=404, detail="Session not found")

    from app.video_export import get_existing_video
    result = get_existing_video(session_id)
    if not result:
        raise HTTPException(status_code=404,
                            detail="Video not ready yet — call POST /video first")

    path, mime = result
    ext = path.suffix
    filename = f"autocad_session_{session_id}{ext}"
    return FileResponse(str(path), media_type=mime,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/sessions/{session_id}/events/{event_id}/image")
def get_autocad_event_image(session_id: int, event_id: int):
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


# ── Narration ─────────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/generate")
def regenerate_autocad_narration(
    session_id: int,
    lang: str = Query(default="zh", description="Output language: zh | en | de"),
):
    conn = get_conn()
    if not conn.execute(
        "SELECT id FROM scribe_sessions WHERE id=?",
        (session_id,),
    ).fetchone():
        raise HTTPException(status_code=404, detail="Session not found")

    sess_row = conn.execute(
        "SELECT target_app, background FROM scribe_sessions WHERE id=?", (session_id,)
    ).fetchone()
    target_exe = (sess_row["target_app"] if sess_row else None) or "acad.exe"
    background = (sess_row["background"] if sess_row else None) or ""

    rows = conn.execute(
        """SELECT e.event_type, e.timestamp, e.app_name, e.window_title,
                  e.uia_element_name, e.uia_element_type, e.uia_automation_id,
                  e.screenshot_path, e.annotation, e.voice_text, e.voice_confidence,
                  fa.shapes_json
           FROM scribe_events e
           LEFT JOIN frame_annotations fa ON fa.event_id = e.id
           WHERE e.session_id=? ORDER BY e.seq""",
        (session_id,),
    ).fetchall()
    events = [dict(r) for r in rows]

    conn.execute(
        "UPDATE scribe_sessions SET status='processing', error_message=NULL WHERE id=?",
        (session_id,),
    )
    conn.commit()

    def _do():
        try:
            narration = _generate_narration_sync(
                events, lang=lang, target_exe=target_exe, background=background
            )
            import sqlite3
            from app.database import DB_PATH
            c = sqlite3.connect(str(DB_PATH))
            c.execute(
                "UPDATE scribe_sessions SET narration_text=?, status='done' WHERE id=?",
                (narration, session_id),
            )
            c.commit(); c.close()
            logger.info("Narration generation complete for session %d", session_id)
        except Exception as exc:
            logger.error("Narration thread failed for session %d: %s", session_id, exc, exc_info=True)
            try:
                import sqlite3
                from app.database import DB_PATH
                c = sqlite3.connect(str(DB_PATH))
                c.execute(
                    "UPDATE scribe_sessions SET status='error', error_message=? WHERE id=?",
                    (str(exc), session_id),
                )
                c.commit(); c.close()
            except Exception:
                pass

    threading.Thread(target=_do, daemon=True).start()
    return {"ok": True, "session_id": session_id, "status": "processing"}


# ── Avatar export ─────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/avatar")
def submit_autocad_avatar(
    session_id: int,
    provider:  str = Query(default="heygen"),
    avatar_id: str = Query(default=""),
    voice_id:  str = Query(default=""),
    api_key:   str = Query(default=""),
):
    conn = get_conn()
    row = conn.execute(
        "SELECT narration_text FROM scribe_sessions WHERE id=?",
        (session_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if not row["narration_text"]:
        raise HTTPException(status_code=422, detail="No narration — run /generate first")

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

    conn.execute(
        "UPDATE scribe_sessions SET avatar_job_id=? WHERE id=?",
        (result.get("job_id"), session_id),
    )
    conn.commit()
    return result


@router.get("/sessions/{session_id}/avatar/status")
def poll_autocad_avatar(
    session_id: int,
    provider: str = Query(default="heygen"),
    api_key:  str = Query(default=""),
):
    conn = get_conn()
    row = conn.execute(
        "SELECT avatar_job_id FROM scribe_sessions WHERE id=?",
        (session_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if not row["avatar_job_id"]:
        raise HTTPException(status_code=422, detail="No avatar job submitted")

    try:
        from app.avatar_export import poll_avatar_job
        result = poll_avatar_job(row["avatar_job_id"], provider, api_key or None)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    if result.get("status") == "done" and result.get("video_url"):
        conn.execute(
            "UPDATE scribe_sessions SET avatar_video_url=? WHERE id=?",
            (result["video_url"], session_id),
        )
        conn.commit()
    return result


# ── Reference ─────────────────────────────────────────────────────────────────

@router.get("/commands")
def get_command_categories():
    """Return known AutoCAD commands grouped by category."""
    return {
        cat: {
            "label":    _CATEGORY_LABELS.get(cat, cat),
            "commands": sorted(cmds),
        }
        for cat, cmds in COMMAND_CATEGORIES.items()
    }