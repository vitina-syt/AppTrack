"""
SQLite database setup and connection management.
Thread-safe single-file local storage — no cloud, no ORM.
"""
import sqlite3
import threading
import sys
import os
from pathlib import Path


def _get_data_dir() -> Path:
    """
    Return a writable data directory.

    - Frozen (PyInstaller exe): %APPDATA%\\AppTrack\\   (always user-writable)
    - Development / uvicorn:    <project_root>/backend/data/
    """
    if getattr(sys, "frozen", False):
        # Running as packaged exe — write to user's AppData, never to Program Files
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(appdata) / "AppTrack"
    # Development: relative to this file → backend/data/
    return Path(__file__).parent.parent / "data"


DATA_DIR = _get_data_dir()
DB_PATH  = DATA_DIR / "apptrack.db"

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    """Return a per-thread SQLite connection (created on first access)."""
    if not hasattr(_local, "conn") or _local.conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _local.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db() -> None:
    """Create tables if they don't exist. Called once at startup."""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            app_name        TEXT    NOT NULL,
            exe_path        TEXT,
            window_title    TEXT,
            started_at      TEXT    NOT NULL,   -- ISO-8601 UTC
            ended_at        TEXT,               -- NULL = still active
            duration_seconds INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_date
            ON sessions(started_at);

        CREATE INDEX IF NOT EXISTS idx_sessions_app
            ON sessions(app_name);

        -- ── Screen Recording ────────────────────────────────────────────
        -- A recording is one continuous capture session (start → stop)
        CREATE TABLE IF NOT EXISTS recordings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT    NOT NULL DEFAULT '',   -- user-editable
            note        TEXT    NOT NULL DEFAULT '',   -- user-editable memo
            started_at  TEXT    NOT NULL,              -- ISO-8601 UTC
            ended_at    TEXT,                          -- NULL = still running
            screenshot_dir TEXT NOT NULL               -- folder holding PNGs
        );

        -- Each row is one captured event inside a recording
        CREATE TABLE IF NOT EXISTS events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            recording_id    INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
            seq             INTEGER NOT NULL,          -- order within recording
            event_type      TEXT    NOT NULL,          -- 'click','scroll','app_open','screenshot','key'
            timestamp       TEXT    NOT NULL,          -- ISO-8601 UTC
            app_name        TEXT,
            window_title    TEXT,
            -- mouse / scroll
            x               INTEGER,
            y               INTEGER,
            button          TEXT,                      -- 'left','right','middle'
            scroll_dx       INTEGER,
            scroll_dy       INTEGER,
            -- screenshot
            screenshot_path TEXT,                      -- relative path inside screenshot_dir
            -- user-editable annotation
            annotation      TEXT    NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_events_recording
            ON events(recording_id, seq);

        -- ── CreoScribe ───────────────────────────────────────────────────
        -- One scribe session = one Creo operation recording with AI narration
        CREATE TABLE IF NOT EXISTS scribe_sessions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            title            TEXT NOT NULL DEFAULT '',
            background       TEXT NOT NULL DEFAULT '',  -- user-provided context for AI narration
            target_app       TEXT NOT NULL DEFAULT 'Creo',   -- process name to monitor
            started_at       TEXT NOT NULL,
            ended_at         TEXT,
            status           TEXT NOT NULL DEFAULT 'recording',  -- recording|processing|done|error
            narration_text   TEXT,          -- Claude-generated narration script
            avatar_video_url TEXT,          -- HeyGen/D-ID output URL
            avatar_job_id    TEXT,          -- HeyGen/D-ID job ID (for polling)
            screenshot_dir   TEXT NOT NULL DEFAULT '',
            error_message    TEXT
        );

        -- Each captured event inside a scribe session
        CREATE TABLE IF NOT EXISTS scribe_events (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id        INTEGER NOT NULL REFERENCES scribe_sessions(id) ON DELETE CASCADE,
            seq               INTEGER NOT NULL,
            event_type        TEXT NOT NULL,  -- uia_invoke|uia_focus|screenshot|voice_segment|app_open
            timestamp         TEXT NOT NULL,
            app_name          TEXT,
            window_title      TEXT,
            uia_element_name  TEXT,
            uia_element_type  TEXT,
            uia_automation_id TEXT,
            screenshot_path   TEXT,
            voice_text        TEXT,
            voice_confidence  REAL,
            annotation        TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_scribe_events_session
            ON scribe_events(session_id, seq);

        -- ── Frame Editor ─────────────────────────────────────────────────
        -- Per-frame title, narration and annotation shapes for the editor
        CREATE TABLE IF NOT EXISTS frame_annotations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL REFERENCES scribe_sessions(id) ON DELETE CASCADE,
            event_id    INTEGER NOT NULL REFERENCES scribe_events(id)   ON DELETE CASCADE,
            seq         INTEGER NOT NULL DEFAULT 0,
            title       TEXT    NOT NULL DEFAULT '',
            narration   TEXT    NOT NULL DEFAULT '',
            shapes_json TEXT    NOT NULL DEFAULT '[]',  -- JSON array of annotation shapes
            UNIQUE(session_id, event_id)
        );
        CREATE INDEX IF NOT EXISTS idx_frame_annotations_session
            ON frame_annotations(session_id);
    """)
    # ── Migrations (add columns to existing tables without data loss) ──────────
    migrations = [
        "ALTER TABLE scribe_sessions ADD COLUMN background TEXT NOT NULL DEFAULT ''",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except Exception:
            pass   # column already exists
    conn.commit()
