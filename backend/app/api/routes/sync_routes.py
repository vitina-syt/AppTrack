"""
Sync routes — /api/sync/*

Two sides share this file:

  Client (local Electron app)
  ─────────────────────────────────────────────────────────────
  POST /api/sync/push/{session_id}
      Package the local session (DB rows + screenshot files) into a ZIP
      and POST it to the central server's /api/sync/receive endpoint.

  GET  /api/sync/status/{session_id}
      Return the sync_status column for one session.

  Server (central, receives uploads)
  ─────────────────────────────────────────────────────────────
  POST /api/sync/receive
      Accept an uploaded session ZIP, unpack it, and import it into
      the server's own database.  Idempotent by origin_session_id +
      origin_host.
"""

import io
import json
import logging
import shutil
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib import request as urllib_request

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.database import get_conn, DB_PATH

logger = logging.getLogger("app.sync_routes")
router = APIRouter(prefix="/api/sync", tags=["sync"])

_DATA_DIR = Path(DB_PATH).parent
_SCREENSHOTS_BASE = _DATA_DIR / "screenshots"

# ── helpers ───────────────────────────────────────────────────────────────────

def _ensure_sync_column() -> None:
    """Add sync_status column to scribe_sessions if it doesn't exist yet."""
    conn = get_conn()
    try:
        conn.execute("ALTER TABLE scribe_sessions ADD COLUMN sync_status TEXT DEFAULT 'local'")
        conn.commit()
    except Exception:
        pass   # already exists


def _build_session_zip(session_id: int) -> bytes:
    """
    Pack the session's DB rows and screenshot files into an in-memory ZIP.
    Layout inside the ZIP:
        session.json          — scribe_sessions row
        events.json           — scribe_events rows (list)
        frames.json           — frame_annotations rows (list)
        screenshots/<name>    — image files
    """
    conn = get_conn()

    sess = conn.execute(
        "SELECT * FROM scribe_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if not sess:
        raise ValueError(f"Session {session_id} not found")

    events = conn.execute(
        "SELECT * FROM scribe_events WHERE session_id=? ORDER BY seq",
        (session_id,),
    ).fetchall()

    frames = conn.execute(
        "SELECT * FROM frame_annotations WHERE session_id=?",
        (session_id,),
    ).fetchall()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("session.json", json.dumps(dict(sess), ensure_ascii=False, default=str))
        zf.writestr("events.json",  json.dumps([dict(e) for e in events], ensure_ascii=False, default=str))
        zf.writestr("frames.json",  json.dumps([dict(f) for f in frames], ensure_ascii=False, default=str))

        shot_dir = Path(dict(sess).get("screenshot_dir", "") or "")
        if shot_dir.exists():
            for ext in ("*.png", "*.jpg", "*.jpeg"):
                for img in sorted(shot_dir.glob(ext)):
                    zf.write(str(img), f"screenshots/{img.name}")

    buf.seek(0)
    return buf.read()


# ── client: push ──────────────────────────────────────────────────────────────

@router.post("/push/{session_id}")
def push_session(
    session_id: int,
    server_url: str = Query(..., description="Central server base URL, e.g. https://myserver.com"),
):
    """
    Package local session data and upload it to the central server.
    Returns the server-assigned session_id on success.
    """
    _ensure_sync_column()
    conn = get_conn()

    sess = conn.execute(
        "SELECT id FROM scribe_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        zip_bytes = _build_session_zip(session_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build session package: {exc}")

    receive_url = server_url.rstrip("/") + "/api/sync/receive"
    import socket
    origin_host = socket.gethostname()

    req = urllib_request.Request(
        receive_url,
        data=zip_bytes,
        headers={
            "Content-Type":        "application/zip",
            "X-Origin-Session-Id": str(session_id),
            "X-Origin-Host":       origin_host,
        },
        method="POST",
    )

    try:
        with urllib_request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
    except Exception as exc:
        logger.error("push_session failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Server unreachable: {exc}")

    # Mark as synced
    conn.execute(
        "UPDATE scribe_sessions SET sync_status='synced' WHERE id=?",
        (session_id,),
    )
    conn.commit()

    return {"ok": True, "server_session_id": result.get("session_id")}


@router.get("/status/{session_id}")
def sync_status(session_id: int):
    """Return the sync_status ('local' | 'synced') of a session."""
    _ensure_sync_column()
    conn = get_conn()
    row = conn.execute(
        "SELECT sync_status FROM scribe_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "sync_status": row["sync_status"] or "local"}


# ── server: receive ───────────────────────────────────────────────────────────

@router.post("/receive")
async def receive_session(request: Request):
    """
    Receive a session ZIP from a client and import it into the server DB.
    Idempotent: if the same (origin_host, origin_session_id) already exists
    the existing server session_id is returned without duplication.
    """
    zip_bytes = await request.body()
    origin_session_id = request.headers.get("X-Origin-Session-Id", "")
    origin_host       = request.headers.get("X-Origin-Host", "unknown")

    if not zip_bytes:
        raise HTTPException(status_code=400, detail="Empty body — expected a ZIP file")

    # ── Check idempotency ──────────────────────────────────────────────────────
    conn = get_conn()
    _ensure_sync_column()

    existing = conn.execute(
        """SELECT id FROM scribe_sessions
           WHERE sync_status=? AND title LIKE ?""",
        ("received", f"%[{origin_host}#{origin_session_id}]%"),
    ).fetchone()
    if existing:
        logger.info("Session already received: host=%s id=%s", origin_host, origin_session_id)
        return {"ok": True, "session_id": existing["id"], "duplicate": True}

    # ── Unpack ZIP ─────────────────────────────────────────────────────────────
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            session_data = json.loads(zf.read("session.json"))
            events_data  = json.loads(zf.read("events.json"))
            frames_data  = json.loads(zf.read("frames.json")) if "frames.json" in zf.namelist() else []
            screenshot_files = [n for n in zf.namelist() if n.startswith("screenshots/")]

            # ── Create session in server DB ────────────────────────────────────
            now = datetime.now(timezone.utc).isoformat()
            # Embed origin info into the title so we can detect duplicates later
            origin_tag = f"[{origin_host}#{origin_session_id}]"
            orig_title = session_data.get("title") or f"Session #{origin_session_id}"
            title = f"{orig_title} {origin_tag}"

            shot_dir_path = _SCREENSHOTS_BASE / f"rcv_{origin_host}_{origin_session_id}"
            shot_dir_path.mkdir(parents=True, exist_ok=True)

            cur = conn.execute(
                """INSERT INTO scribe_sessions
                   (title, background, target_app, started_at, ended_at,
                    status, narration_text, screenshot_dir, sync_status)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    title,
                    session_data.get("background", ""),
                    session_data.get("target_app", ""),
                    session_data.get("started_at", now),
                    session_data.get("ended_at"),
                    "done",
                    session_data.get("narration_text"),
                    str(shot_dir_path),
                    "received",
                ),
            )
            new_session_id = cur.lastrowid

            # ── Import events ──────────────────────────────────────────────────
            for ev in events_data:
                conn.execute(
                    """INSERT INTO scribe_events
                       (session_id, seq, event_type, timestamp, app_name, window_title,
                        uia_element_name, uia_element_type, uia_automation_id,
                        screenshot_path, voice_text, voice_confidence, annotation)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        new_session_id,
                        ev.get("seq", 0),
                        ev.get("event_type", ""),
                        ev.get("timestamp", now),
                        ev.get("app_name"),
                        ev.get("window_title"),
                        ev.get("uia_element_name"),
                        ev.get("uia_element_type"),
                        ev.get("uia_automation_id"),
                        ev.get("screenshot_path"),
                        ev.get("voice_text"),
                        ev.get("voice_confidence"),
                        ev.get("annotation", ""),
                    ),
                )

            # ── Import frame annotations ───────────────────────────────────────
            # Map old event IDs to new ones via seq
            old_to_new_event: dict[int, int] = {}
            old_events = {e.get("seq"): e.get("id") for e in events_data}
            new_event_rows = conn.execute(
                "SELECT id, seq FROM scribe_events WHERE session_id=?",
                (new_session_id,),
            ).fetchall()
            for row in new_event_rows:
                for seq, old_id in old_events.items():
                    if row["seq"] == seq and old_id:
                        old_to_new_event[old_id] = row["id"]

            for fa in frames_data:
                new_event_id = old_to_new_event.get(fa.get("event_id"))
                if new_event_id is None:
                    continue
                conn.execute(
                    """INSERT OR IGNORE INTO frame_annotations
                       (session_id, event_id, seq, title, narration, shapes_json)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        new_session_id,
                        new_event_id,
                        fa.get("seq", 0),
                        fa.get("title", ""),
                        fa.get("narration", ""),
                        fa.get("shapes_json", "[]"),
                    ),
                )

            conn.commit()

            # ── Extract screenshots ────────────────────────────────────────────
            for name in screenshot_files:
                fname = Path(name).name
                if fname:
                    data = zf.read(name)
                    (shot_dir_path / fname).write_bytes(data)

    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid ZIP file")
    except Exception as exc:
        logger.exception("receive_session failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Import failed: {exc}")

    logger.info("Received session from %s (origin #%s) → local #%s",
                origin_host, origin_session_id, new_session_id)
    return {"ok": True, "session_id": new_session_id}
