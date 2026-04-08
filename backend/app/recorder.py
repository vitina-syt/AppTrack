"""
Screen recorder — Autoscribe-style capture.

Captures:
  • Mouse clicks (left / right / middle)
  • Mouse scroll
  • App focus changes (new foreground window)
  • Periodic screenshots (every N seconds while recording)
  • On-click screenshots (screenshot taken immediately after each click)

All events are written to SQLite; screenshots saved as JPEG in a per-recording
folder under  data/screenshots/<recording_id>/.
"""
import threading
import time
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("app.recorder")

try:
    import mss
    import mss.tools
    _MSS = True
except ImportError:
    _MSS = False
    logger.warning("mss not available — screenshots disabled")

try:
    from PIL import Image
    _PIL = True
except ImportError:
    _PIL = False

try:
    from pynput import mouse as _mouse
    _PYNPUT = True
except ImportError:
    _PYNPUT = False
    logger.warning("pynput not available — mouse listener disabled")

try:
    import win32gui
    import win32process
    import psutil
    _WIN32 = True
except ImportError:
    _WIN32 = False


# ── helpers ──────────────────────────────────────────────────────────────────

SCREENSHOTS_BASE = Path(__file__).parent.parent / "data" / "screenshots"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_foreground() -> tuple[Optional[str], Optional[str]]:
    """Return (app_name, window_title) of the current foreground window."""
    if not _WIN32:
        return None, None
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return psutil.Process(pid).name(), title
    except Exception:
        return None, None


def _take_screenshot(folder: Path, filename: str) -> Optional[str]:
    """Capture full screen, save as JPEG. Returns relative filename or None."""
    if not _MSS:
        return None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / filename
        with mss.mss() as sct:
            raw = sct.grab(sct.monitors[0])  # monitor 0 = all screens combined
        if _PIL:
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            img.save(str(path), format="JPEG", quality=75)
        else:
            # Fallback: save as PNG via mss
            png_path = path.with_suffix(".png")
            mss.tools.to_png(raw.rgb, raw.size, output=str(png_path))
            return png_path.name
        return path.name
    except Exception as exc:
        logger.debug("Screenshot failed: %s", exc)
        return None


# ── Recorder ─────────────────────────────────────────────────────────────────

class ScreenRecorder:
    """One instance manages a single recording session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._recording_id: Optional[int] = None
        self._seq = 0
        self._screenshot_folder: Optional[Path] = None
        self._conn: Optional[sqlite3.Connection] = None
        self._mouse_listener = None
        self._periodic_thread: Optional[threading.Thread] = None
        self._app_poll_thread: Optional[threading.Thread] = None
        self._screenshot_interval = 30  # seconds
        self._app_poll_interval = 2     # seconds
        self._last_app: Optional[str] = None

    # ── public ───────────────────────────────────────────────────────────

    def start(
        self,
        title: str = "",
        screenshot_interval: int = 30,
        on_click_screenshot: bool = True,
    ) -> int:
        """Start a new recording. Returns recording_id."""
        with self._lock:
            if self._running:
                raise RuntimeError("Recording already in progress")
            self._screenshot_interval = screenshot_interval
            self._on_click_screenshot = on_click_screenshot
            self._seq = 0

        from app.database import DB_PATH
        self._conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        now = _utcnow()
        cur = self._conn.execute(
            "INSERT INTO recordings (title, started_at, screenshot_dir) VALUES (?,?,?)",
            (title, now, ""),  # screenshot_dir filled below
        )
        self._conn.commit()
        rid = cur.lastrowid
        self._recording_id = rid

        folder = SCREENSHOTS_BASE / str(rid)
        folder.mkdir(parents=True, exist_ok=True)
        self._screenshot_folder = folder
        self._conn.execute(
            "UPDATE recordings SET screenshot_dir=? WHERE id=?",
            (str(folder), rid),
        )
        self._conn.commit()

        with self._lock:
            self._running = True
            self._last_app = None

        # Capture initial screenshot
        self._save_screenshot(trigger="recording_start")

        # Start mouse listener
        if _PYNPUT:
            self._mouse_listener = _mouse.Listener(
                on_click=self._on_click,
                on_scroll=self._on_scroll,
            )
            self._mouse_listener.start()

        # Periodic screenshot thread
        self._periodic_thread = threading.Thread(
            target=self._periodic_loop, daemon=True, name="Recorder-periodic"
        )
        self._periodic_thread.start()

        # App focus change poll thread
        self._app_poll_thread = threading.Thread(
            target=self._app_poll_loop, daemon=True, name="Recorder-apppoll"
        )
        self._app_poll_thread.start()

        logger.info("Recording started id=%d folder=%s", rid, folder)
        return rid

    def stop(self) -> Optional[int]:
        """Stop recording. Returns recording_id."""
        with self._lock:
            if not self._running:
                return None
            self._running = False
            rid = self._recording_id

        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None

        if self._periodic_thread:
            self._periodic_thread.join(timeout=5)
        if self._app_poll_thread:
            self._app_poll_thread.join(timeout=5)

        if self._conn:
            self._conn.execute(
                "UPDATE recordings SET ended_at=? WHERE id=?",
                (_utcnow(), rid),
            )
            self._conn.commit()
            self._conn.close()
            self._conn = None

        logger.info("Recording stopped id=%d", rid)
        self._recording_id = None
        return rid

    @property
    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "recording_id": self._recording_id,
                "events_captured": self._seq,
            }

    # ── internal ─────────────────────────────────────────────────────────

    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def _write_event(self, **kwargs) -> None:
        """Insert one event row. Thread-safe via WAL + Python lock."""
        if not self._conn:
            return
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join("?" * len(kwargs))
        try:
            self._conn.execute(
                f"INSERT INTO events ({cols}) VALUES ({placeholders})",
                list(kwargs.values()),
            )
            self._conn.commit()
        except Exception as exc:
            logger.debug("write_event failed: %s", exc)

    def _save_screenshot(self, trigger: str = "periodic") -> Optional[str]:
        if not self._screenshot_folder:
            return None
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        fname = f"{ts}.jpg"
        rel = _take_screenshot(self._screenshot_folder, fname)
        if rel:
            app, title = _get_foreground()
            self._write_event(
                recording_id=self._recording_id,
                seq=self._next_seq(),
                event_type="screenshot",
                timestamp=_utcnow(),
                app_name=app,
                window_title=title,
                screenshot_path=rel,
                annotation=trigger,
            )
        return rel

    # ── mouse callbacks (pynput, runs in listener thread) ────────────────

    def _on_click(self, x, y, button, pressed) -> None:
        if not pressed:
            return
        with self._lock:
            if not self._running:
                return
            rid = self._recording_id
            do_screenshot = self._on_click_screenshot

        app, title = _get_foreground()
        self._write_event(
            recording_id=rid,
            seq=self._next_seq(),
            event_type="click",
            timestamp=_utcnow(),
            app_name=app,
            window_title=title,
            x=x,
            y=y,
            button=button.name,
        )
        if do_screenshot:
            self._save_screenshot(trigger="click")

    def _on_scroll(self, x, y, dx, dy) -> None:
        with self._lock:
            if not self._running:
                return
            rid = self._recording_id

        app, title = _get_foreground()
        self._write_event(
            recording_id=rid,
            seq=self._next_seq(),
            event_type="scroll",
            timestamp=_utcnow(),
            app_name=app,
            window_title=title,
            x=x,
            y=y,
            scroll_dx=dx,
            scroll_dy=dy,
        )

    # ── background threads ───────────────────────────────────────────────

    def _periodic_loop(self) -> None:
        while True:
            time.sleep(self._screenshot_interval)
            with self._lock:
                if not self._running:
                    break
            self._save_screenshot(trigger="periodic")

    def _app_poll_loop(self) -> None:
        """Detect foreground window changes and log as app_open events."""
        while True:
            time.sleep(self._app_poll_interval)
            with self._lock:
                if not self._running:
                    break
                rid = self._recording_id
                last = self._last_app

            app, title = _get_foreground()
            if app and app != last:
                with self._lock:
                    self._last_app = app
                self._write_event(
                    recording_id=rid,
                    seq=self._next_seq(),
                    event_type="app_open",
                    timestamp=_utcnow(),
                    app_name=app,
                    window_title=title,
                )


# Module-level singleton
recorder = ScreenRecorder()