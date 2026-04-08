"""
Windows foreground-window tracker.
Polls GetForegroundWindow() every N seconds in a background thread,
writes session records to SQLite.
"""
import threading
import time
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

logger = logging.getLogger("app.tracker")

try:
    import win32gui
    import win32process
    import psutil
    _WIN32 = True
except ImportError:
    _WIN32 = False
    logger.warning("pywin32 / psutil not available — tracker will run in demo mode")


def _get_foreground() -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (app_name, exe_path, window_title) of the current foreground window."""
    if not _WIN32:
        # Demo mode: simulate a rotating set of apps
        import random
        demo = [("AutoCAD.exe", r"C:\Program Files\Autodesk\AutoCAD\AutoCAD.exe", "Drawing1.dwg"),
                ("OUTLOOK.EXE", r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE", "Inbox — Outlook"),
                ("chrome.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe", "Google Chrome"),
                ("Code.exe", r"C:\Users\user\AppData\Local\Programs\Microsoft VS Code\Code.exe", "editor — VS Code"),
                ("EXCEL.EXE", r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE", "Book1.xlsx — Excel")]
        return random.choice(demo)
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc = psutil.Process(pid)
        return proc.name(), proc.exe(), title
    except Exception as exc:
        logger.debug("GetForegroundWindow failed: %s", exc)
        return None, None, None


class AppTracker:
    """Singleton tracker — use the module-level `tracker` instance."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._poll_interval = 5          # seconds, changeable before start
        self._current_app: Optional[str] = None
        self._current_exe: Optional[str] = None
        self._current_title: Optional[str] = None

    # ── public interface ────────────────────────────────────────────────

    def start(self, poll_interval: int = 5, db_path: Optional[Path] = None) -> None:
        with self._lock:
            if self._running:
                return
            self._poll_interval = poll_interval
            self._db_path = db_path
            self._running = True
        self._thread = threading.Thread(target=self._run, name="AppTracker", daemon=True)
        self._thread.start()
        logger.info("Tracker started (interval=%ds)", poll_interval)

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
        if self._thread:
            self._thread.join(timeout=self._poll_interval + 2)
        logger.info("Tracker stopped")

    @property
    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "current_app": self._current_app,
                "current_exe": self._current_exe,
                "current_title": self._current_title,
                "poll_interval": self._poll_interval,
            }

    # ── internal loop ───────────────────────────────────────────────────

    def _run(self) -> None:
        from app.database import DB_PATH
        db_path = self._db_path or DB_PATH
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")

        current_app: Optional[str] = None
        current_session_id: Optional[int] = None

        while True:
            with self._lock:
                if not self._running:
                    break
                interval = self._poll_interval

            app_name, exe_path, title = _get_foreground()
            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            if app_name != current_app:
                # Close previous session
                if current_session_id is not None:
                    conn.execute(
                        """UPDATE sessions
                           SET ended_at = ?,
                               duration_seconds = CAST(
                                   (julianday(?) - julianday(started_at)) * 86400 AS INTEGER)
                           WHERE id = ?""",
                        (now_utc, now_utc, current_session_id),
                    )
                    conn.commit()

                # Open new session
                if app_name:
                    cur = conn.execute(
                        "INSERT INTO sessions (app_name, exe_path, window_title, started_at) VALUES (?,?,?,?)",
                        (app_name, exe_path, title, now_utc),
                    )
                    conn.commit()
                    current_session_id = cur.lastrowid
                    current_app = app_name
                else:
                    current_session_id = None
                    current_app = None

                with self._lock:
                    self._current_app = current_app
                    self._current_exe = exe_path
                    self._current_title = title

            time.sleep(interval)

        # Graceful shutdown: close the in-progress session
        if current_session_id is not None:
            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute(
                """UPDATE sessions
                   SET ended_at = ?,
                       duration_seconds = CAST(
                           (julianday(?) - julianday(started_at)) * 86400 AS INTEGER)
                   WHERE id = ?""",
                (now_utc, now_utc, current_session_id),
            )
            conn.commit()
            with self._lock:
                self._current_app = None
                self._current_exe = None
                self._current_title = None
        conn.close()


# Module-level singleton
tracker = AppTracker()
