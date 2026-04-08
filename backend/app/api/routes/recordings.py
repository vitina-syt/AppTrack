"""
Recording control + CRUD endpoints.

POST   /api/recordings/start          — start a new recording
POST   /api/recordings/stop           — stop the active recording
GET    /api/recordings/status         — recorder status
GET    /api/recordings                — list all recordings
GET    /api/recordings/{id}           — get one recording
PATCH  /api/recordings/{id}           — edit title / note
DELETE /api/recordings/{id}           — delete recording + events + screenshots

GET    /api/recordings/{id}/events                  — list events (filterable)
PATCH  /api/recordings/{id}/events/{event_id}       — edit annotation
GET    /api/recordings/{id}/events/{event_id}/image — serve screenshot JPEG
"""
import shutil
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.database import get_conn
from app.recorder import recorder
from app.models import (
    Recording, RecordingUpdate,
    Event, EventAnnotationUpdate,
    RecorderStatus,
)

router = APIRouter(prefix="/api/recordings", tags=["recordings"])


# ── Recorder control ─────────────────────────────────────────────────────────

@router.post("/start", response_model=RecorderStatus)
def start_recording(
    title: str = Query(default="", description="Optional recording title"),
    screenshot_interval: int = Query(default=30, ge=5, le=300),
    on_click_screenshot: bool = Query(default=True),
):
    try:
        recorder.start(
            title=title,
            screenshot_interval=screenshot_interval,
            on_click_screenshot=on_click_screenshot,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return recorder.status


@router.post("/stop", response_model=RecorderStatus)
def stop_recording():
    recorder.stop()
    return recorder.status


@router.get("/status", response_model=RecorderStatus)
def get_status():
    return recorder.status


# ── Recordings CRUD ──────────────────────────────────────────────────────────

@router.get("", response_model=List[Recording])
def list_recordings(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    conn = get_conn()
    rows = conn.execute(
        """SELECT r.*,
                  (SELECT COUNT(*) FROM events e WHERE e.recording_id = r.id) AS event_count
           FROM recordings r
           ORDER BY r.started_at DESC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/{recording_id}", response_model=Recording)
def get_recording(recording_id: int):
    conn = get_conn()
    row = conn.execute(
        """SELECT r.*,
                  (SELECT COUNT(*) FROM events e WHERE e.recording_id = r.id) AS event_count
           FROM recordings r WHERE r.id = ?""",
        (recording_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Recording not found")
    return dict(row)


@router.patch("/{recording_id}", response_model=Recording)
def update_recording(recording_id: int, body: RecordingUpdate):
    conn = get_conn()
    row = conn.execute("SELECT id FROM recordings WHERE id=?", (recording_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Recording not found")

    if body.title is not None:
        conn.execute("UPDATE recordings SET title=? WHERE id=?", (body.title, recording_id))
    if body.note is not None:
        conn.execute("UPDATE recordings SET note=? WHERE id=?", (body.note, recording_id))
    conn.commit()
    return get_recording(recording_id)


@router.delete("/{recording_id}")
def delete_recording(recording_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT screenshot_dir FROM recordings WHERE id=?", (recording_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Recording not found")

    # Delete screenshot folder
    folder = Path(row["screenshot_dir"])
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)

    conn.execute("DELETE FROM recordings WHERE id=?", (recording_id,))
    conn.commit()
    return {"ok": True}


# ── Events ───────────────────────────────────────────────────────────────────

@router.get("/{recording_id}/events", response_model=List[Event])
def list_events(
    recording_id: int,
    event_type: Optional[str] = Query(default=None, description="Filter by type: click|scroll|app_open|screenshot"),
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
):
    conn = get_conn()
    # Verify recording exists
    if not conn.execute("SELECT id FROM recordings WHERE id=?", (recording_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Recording not found")

    if event_type:
        rows = conn.execute(
            "SELECT * FROM events WHERE recording_id=? AND event_type=? ORDER BY seq LIMIT ? OFFSET ?",
            (recording_id, event_type, limit, offset),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM events WHERE recording_id=? ORDER BY seq LIMIT ? OFFSET ?",
            (recording_id, limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


@router.patch("/{recording_id}/events/{event_id}", response_model=Event)
def update_event_annotation(
    recording_id: int,
    event_id: int,
    body: EventAnnotationUpdate,
):
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM events WHERE id=? AND recording_id=?",
        (event_id, recording_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")

    conn.execute("UPDATE events SET annotation=? WHERE id=?", (body.annotation, event_id))
    conn.commit()
    updated = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    return dict(updated)


@router.get("/{recording_id}/events/{event_id}/image")
def get_event_image(recording_id: int, event_id: int):
    conn = get_conn()
    row = conn.execute(
        """SELECT e.screenshot_path, r.screenshot_dir
           FROM events e
           JOIN recordings r ON r.id = e.recording_id
           WHERE e.id=? AND e.recording_id=? AND e.event_type='screenshot'""",
        (event_id, recording_id),
    ).fetchone()
    if not row or not row["screenshot_path"]:
        raise HTTPException(status_code=404, detail="No screenshot for this event")

    path = Path(row["screenshot_dir"]) / row["screenshot_path"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Screenshot file not found")

    media_type = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return FileResponse(str(path), media_type=media_type)