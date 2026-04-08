"""
CreoScribe Agent — orchestrator for the three-input pipeline.

Inputs
------
  • UIA events  (CreoUiaMonitor)
  • Voice input (VoiceCapture)
  • Screenshots (mss, reused from recorder logic)

On stop, the agent calls Claude to synthesise all events into a professional
narration script, then stores the result in the scribe_sessions table.

Public API
----------
    agent = ScribeAgent()
    session_id = agent.start(title="Creo Demo", target_app="xtop.exe")
    agent.stop()           # blocks until narration is generated
    agent.status           # dict
"""
import threading
import time
import sqlite3
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger("app.scribe_agent")

try:
    import mss
    import mss.tools
    _MSS = True
except ImportError:
    _MSS = False

try:
    from PIL import Image
    _PIL = True
except ImportError:
    _PIL = False

try:
    import anthropic
    _ANTHROPIC = True
except ImportError:
    _ANTHROPIC = False
    logger.info("anthropic SDK not installed — narration generation will be skipped")

from app.creo_uia import CreoUiaMonitor
from app.voice_capture import VoiceCapture

SCREENSHOTS_BASE = Path(__file__).parent.parent / "data" / "scribe_screenshots"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _take_screenshot(folder: Path, filename: str) -> Optional[str]:
    if not _MSS:
        return None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / filename
        with mss.mss() as sct:
            raw = sct.grab(sct.monitors[0])
        if _PIL:
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            img.save(str(path), format="JPEG", quality=70)
        else:
            png = path.with_suffix(".png")
            mss.tools.to_png(raw.rgb, raw.size, output=str(png))
            return png.name
        return path.name
    except Exception as exc:
        logger.debug("Screenshot failed: %s", exc)
        return None


def _build_narration_prompt(events: List[dict], target_app: str) -> str:
    """Build the Claude prompt from captured events."""
    # Group events by type
    uia_events   = [e for e in events if e["event_type"] in ("uia_invoke", "uia_focus")]
    voice_segs   = [e for e in events if e["event_type"] == "voice_segment"]
    screenshots  = [e for e in events if e["event_type"] == "screenshot"]

    lines = [
        f"You are a technical writer creating a professional narration script for a {target_app} tutorial video.",
        "",
        "Below is a chronological log of what happened during the recording session.",
        "Your task: synthesise these raw events into a clear, engaging narration script.",
        "",
        "Requirements:",
        "- Write in the second person ('In this step, you will...')",
        "- Group related actions into logical tutorial steps",
        "- Explain *why* each action matters, not just what was clicked",
        "- Keep each step concise (1-3 sentences)",
        "- Use professional CAD/engineering terminology where appropriate",
        "- If voice notes are present, incorporate and refine them",
        "- Output only the narration script, no meta-commentary",
        "",
        "═══ CAPTURED EVENTS ═══",
        "",
    ]

    if uia_events:
        lines.append("── UI INTERACTIONS ──")
        for e in uia_events[:80]:   # cap to avoid huge prompts
            name = e.get("uia_element_name", "")
            ctrl = e.get("uia_element_type", "")
            etype = e.get("event_type", "")
            ts = e.get("timestamp", "")
            tag = "Clicked" if etype == "uia_invoke" else "Focused"
            lines.append(f"  [{ts}] {tag}: {name} ({ctrl})")
        lines.append("")

    if voice_segs:
        lines.append("── VOICE NOTES (raw) ──")
        for e in voice_segs:
            lines.append(f"  [{e.get('timestamp','')}] {e.get('voice_text','')}")
        lines.append("")

    if screenshots:
        lines.append(f"── SCREENSHOTS ── ({len(screenshots)} captured)")
        for e in screenshots:
            lines.append(f"  [{e.get('timestamp','')}] {e.get('window_title','')}")
        lines.append("")

    lines.append("═══ END OF EVENTS ═══")
    lines.append("")
    lines.append("Write the narration script now:")

    return "\n".join(lines)


async def _generate_narration_async(events: List[dict], target_app: str) -> str:
    """Call Claude asynchronously to generate narration text."""
    if not _ANTHROPIC:
        return _fallback_narration(events, target_app)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — using fallback narration")
        return _fallback_narration(events, target_app)

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        prompt = _build_narration_prompt(events, target_app)
        message = await client.messages.create(
            model="claude-opus-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception as exc:
        logger.error("Claude narration failed: %s", exc)
        return _fallback_narration(events, target_app)


def _generate_narration_sync(events: List[dict], target_app: str) -> str:
    """Synchronous version (runs in thread pool)."""
    if not _ANTHROPIC:
        return _fallback_narration(events, target_app)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — using fallback narration")
        return _fallback_narration(events, target_app)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        prompt = _build_narration_prompt(events, target_app)
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception as exc:
        logger.error("Claude narration failed: %s", exc)
        return _fallback_narration(events, target_app)


def _fallback_narration(events: List[dict], target_app: str) -> str:
    """Simple rule-based narration when Claude is unavailable."""
    uia = [e for e in events if e["event_type"] == "uia_invoke"]
    voice = [e for e in events if e["event_type"] == "voice_segment"]
    lines = [f"[{target_app} Tutorial — Auto-generated narration]\n"]

    if voice:
        lines.append("Voice notes recorded during session:")
        for seg in voice:
            lines.append(f"  • {seg.get('voice_text', '')}")
        lines.append("")

    if uia:
        lines.append("UI operations performed:")
        for e in uia[:30]:
            lines.append(f"  • {e.get('uia_element_name', '')} ({e.get('uia_element_type', '')})")

    if not uia and not voice:
        lines.append("No interactive events were captured in this session.")

    return "\n".join(lines)


# ── ScribeAgent ──────────────────────────────────────────────────────────────

class ScribeAgent:
    """Orchestrates UIA, voice, and screenshot capture into a scribe session."""

    def __init__(self) -> None:
        self._lock   = threading.Lock()
        self._running = False
        self._session_id: Optional[int] = None
        self._seq    = 0
        self._uia_monitor: Optional[CreoUiaMonitor] = None
        self._voice:       Optional[VoiceCapture]   = None
        self._periodic_thread: Optional[threading.Thread] = None
        self._conn:  Optional[sqlite3.Connection]   = None
        self._screenshot_folder: Optional[Path]     = None
        self._screenshot_interval = 30

    # ── public ───────────────────────────────────────────────────────────

    def start(
        self,
        title: str = "",
        target_app: str = "xtop.exe",
        screenshot_interval: int = 30,
        enable_voice: bool = True,
        enable_uia: bool = True,
    ) -> int:
        """
        Start a new scribe session. Returns session_id.
        Raises RuntimeError if already running.
        """
        with self._lock:
            if self._running:
                raise RuntimeError("Scribe session already in progress")
            self._screenshot_interval = screenshot_interval

        from app.database import DB_PATH
        self._conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        now = _utcnow()
        cur = self._conn.execute(
            """INSERT INTO scribe_sessions
               (title, target_app, started_at, status, screenshot_dir)
               VALUES (?,?,?,?,?)""",
            (title, target_app, now, "recording", ""),
        )
        self._conn.commit()
        sid = cur.lastrowid
        self._session_id = sid

        folder = SCREENSHOTS_BASE / str(sid)
        folder.mkdir(parents=True, exist_ok=True)
        self._screenshot_folder = folder
        self._conn.execute(
            "UPDATE scribe_sessions SET screenshot_dir=? WHERE id=?",
            (str(folder), sid),
        )
        self._conn.commit()

        with self._lock:
            self._running = True
            self._seq = 0

        # Initial screenshot
        self._save_screenshot("session_start")

        # UIA monitor
        if enable_uia:
            self._uia_monitor = CreoUiaMonitor(target_app=target_app)
            self._uia_monitor.start(self._on_uia_event)

        # Voice capture
        if enable_voice:
            audio_dir = SCREENSHOTS_BASE / str(sid) / "audio"
            self._voice = VoiceCapture(audio_dir=audio_dir)
            self._voice.start(self._on_voice_segment)

        # Periodic screenshot thread
        self._periodic_thread = threading.Thread(
            target=self._periodic_loop, name="Scribe-periodic", daemon=True
        )
        self._periodic_thread.start()

        logger.info("ScribeAgent started session_id=%d target=%s", sid, target_app)
        return sid

    def stop(self, generate_narration: bool = True) -> Optional[int]:
        """
        Stop recording. Optionally runs narration generation (blocking, may
        take 10-30 s). Returns session_id.
        """
        with self._lock:
            if not self._running:
                return None
            self._running = False
            sid = self._session_id

        if self._uia_monitor:
            self._uia_monitor.stop()
            self._uia_monitor = None

        if self._voice:
            self._voice.stop()
            self._voice = None

        if self._periodic_thread:
            self._periodic_thread.join(timeout=5)

        # Mark session ended
        if self._conn:
            self._conn.execute(
                "UPDATE scribe_sessions SET ended_at=?, status=? WHERE id=?",
                (_utcnow(), "processing" if generate_narration else "done", sid),
            )
            self._conn.commit()

        if generate_narration and sid is not None:
            self._run_narration(sid)

        if self._conn:
            self._conn.close()
            self._conn = None

        self._session_id = None
        logger.info("ScribeAgent stopped session_id=%d", sid)
        return sid

    @property
    def status(self) -> dict:
        with self._lock:
            voice_count = len(self._voice.drain()) if self._voice else 0
            return {
                "running":       self._running,
                "session_id":    self._session_id,
                "events_captured": self._seq,
                "voice_segments": voice_count,
                "uia_events":    sum(
                    1 for _ in []  # placeholder; detailed count via DB
                ),
            }

    # ── narration ────────────────────────────────────────────────────────

    def _run_narration(self, sid: int) -> None:
        """Load events from DB, call Claude, store result."""
        from app.database import DB_PATH
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row

            # Fetch target app name
            sess_row = conn.execute(
                "SELECT target_app FROM scribe_sessions WHERE id=?", (sid,)
            ).fetchone()
            target_app = sess_row["target_app"] if sess_row else "Creo"

            rows = conn.execute(
                """SELECT event_type, timestamp, app_name, window_title,
                          uia_element_name, uia_element_type, uia_automation_id,
                          screenshot_path, voice_text, voice_confidence
                   FROM scribe_events
                   WHERE session_id=?
                   ORDER BY seq""",
                (sid,),
            ).fetchall()
            events = [dict(r) for r in rows]
            conn.close()

            narration = _generate_narration_sync(events, target_app)

            from app.database import DB_PATH as DP2
            conn2 = sqlite3.connect(str(DP2))
            conn2.execute(
                "UPDATE scribe_sessions SET narration_text=?, status='done' WHERE id=?",
                (narration, sid),
            )
            conn2.commit()
            conn2.close()
            logger.info("Narration generated for session %d (%d chars)", sid, len(narration))

        except Exception as exc:
            logger.error("Narration generation failed for session %d: %s", sid, exc)
            try:
                from app.database import DB_PATH as DP3
                conn3 = sqlite3.connect(str(DP3))
                conn3.execute(
                    "UPDATE scribe_sessions SET status='error', error_message=? WHERE id=?",
                    (str(exc), sid),
                )
                conn3.commit()
                conn3.close()
            except Exception:
                pass

    # ── event handlers ────────────────────────────────────────────────────

    def _on_uia_event(self, event: dict) -> None:
        with self._lock:
            if not self._running:
                return
            rid = self._session_id
        self._write_event(
            session_id=rid,
            event_type=event.get("event_type", "uia_focus"),
            app_name=event.get("app_name"),
            window_title=event.get("window_title"),
            uia_element_name=event.get("uia_element_name"),
            uia_element_type=event.get("uia_element_type"),
            uia_automation_id=event.get("uia_automation_id"),
        )

    def _on_voice_segment(self, seg: dict) -> None:
        with self._lock:
            if not self._running:
                return
            rid = self._session_id
        self._write_event(
            session_id=rid,
            event_type="voice_segment",
            voice_text=seg.get("voice_text"),
            voice_confidence=seg.get("voice_confidence"),
        )

    # ── helpers ───────────────────────────────────────────────────────────

    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def _write_event(self, **kwargs) -> None:
        if not self._conn:
            return
        kwargs.setdefault("timestamp", _utcnow())
        kwargs["seq"] = self._next_seq()
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join("?" * len(kwargs))
        try:
            self._conn.execute(
                f"INSERT INTO scribe_events ({cols}) VALUES ({placeholders})",
                list(kwargs.values()),
            )
            self._conn.commit()
        except Exception as exc:
            logger.debug("write_event failed: %s", exc)

    def _save_screenshot(self, trigger: str = "periodic") -> None:
        if not self._screenshot_folder:
            return
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        fname = f"{ts}.jpg"
        rel = _take_screenshot(self._screenshot_folder, fname)
        if rel:
            with self._lock:
                rid = self._session_id
            self._write_event(
                session_id=rid,
                event_type="screenshot",
                screenshot_path=rel,
                annotation=trigger,
            )

    def _periodic_loop(self) -> None:
        while True:
            time.sleep(self._screenshot_interval)
            with self._lock:
                if not self._running:
                    break
            self._save_screenshot("periodic")


# Module-level singleton
scribe_agent = ScribeAgent()