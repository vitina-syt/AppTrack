"""
AutoCAD Scribe Agent.

Extends ScribeAgent with:
  • AutoCAD COM event capture  (autocad_monitor.AutoCADMonitor)
  • AutoCAD-aware Claude prompt (CAD terminology, command categories)
  • Same persistence layer as ScribeAgent (scribe_sessions / scribe_events)

Public API
----------
    session_id = autocad_agent.start(title="My Demo")
    autocad_agent.stop()
    autocad_agent.status  → dict
"""
import threading
import time
import sqlite3
import logging
import os
import queue
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger("app.autocad_agent")

try:
    import mss, mss.tools
    _MSS = True
except ImportError:
    _MSS = False

try:
    from PIL import Image
    _PIL = True
except ImportError:
    _PIL = False

try:
    from app.gpt_assistant import GPTAssistant
    _GPT = True
except Exception:
    _GPT = False

from app.autocad_monitor import AutoCADMonitor, categorize_command, COMMAND_CATEGORIES
from app.voice_capture import VoiceCapture

SCREENSHOTS_BASE = Path(__file__).parent.parent / "data" / "autocad_screenshots"


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
            # Scale down to 1280 wide max — reduces file size & I/O pressure
            max_w = 1280
            if img.width > max_w:
                ratio = max_w / img.width
                img = img.resize(
                    (max_w, int(img.height * ratio)),
                    Image.Resampling.LANCZOS,
                )
            img.save(str(path), format="JPEG", quality=60)
        else:
            png = path.with_suffix(".png")
            mss.tools.to_png(raw.rgb, raw.size, output=str(png))
            return png.name
        return path.name
    except Exception as exc:
        logger.warning("Screenshot failed: %s", exc)
        return None


# ── AutoCAD-specific Claude prompt ────────────────────────────────────────────

_CATEGORY_LABELS = {
    "draw":     "2D Drawing",
    "edit":     "Editing & Modification",
    "3d":       "3D Modeling",
    "annotate": "Annotation & Dimensioning",
    "view":     "View & Display",
    "layer":    "Layer Management",
    "block":    "Blocks & References",
    "file":     "File Operations",
    "other":    "Other",
}


_LANG_INSTRUCTIONS = {
    "zh": (
        "请用**中文**撰写解说词脚本。",
        "你是一位资深 CAD 讲师，正在为 AutoCAD 教程视频撰写专业解说词脚本。",
        '请使用第二人称（例如"首先，您需要……"）逐步讲解操作步骤。',
    ),
    "en": (
        "Write the narration script in **English**.",
        "You are a senior CAD instructor creating a professional narration script for an AutoCAD tutorial video.",
        "Write a clear, step-by-step narration in the second person ('First, you will...').",
    ),
    "de": (
        "Bitte schreiben Sie das Skript auf **Deutsch**.",
        "Sie sind ein erfahrener CAD-Dozent und erstellen ein professionelles Kommentarskript für ein AutoCAD-Tutorial.",
        "Schreiben Sie eine klare Schritt-für-Schritt-Erklärung in der zweiten Person ('Zuerst werden Sie...').",
    ),
}


def _build_autocad_prompt(events: List[dict], lang: str = "zh") -> str:
    """Build an AutoCAD-specific GPT prompt from captured events."""

    # Separate command events from others
    cmd_events   = [e for e in events if "acad_cmd" in (e.get("uia_element_type") or "")]
    obj_events   = [e for e in events if "acad_object" in (e.get("uia_element_type") or "")]
    voice_segs   = [e for e in events if e["event_type"] == "voice_segment"]
    screenshots  = [e for e in events if e["event_type"] == "screenshot"]
    window_evts  = [e for e in events if e["event_type"] in ("app_open", "uia_focus")
                    and "acad_cmd" not in (e.get("uia_element_type") or "")]

    # Group commands by category for summary
    cat_counts: dict[str, list] = {}
    for e in cmd_events:
        typ = e.get("uia_element_type", "")
        cat = typ.split(":")[-1] if ":" in typ else "other"
        cat_counts.setdefault(cat, []).append(
            e.get("uia_element_name", "").replace(" ✓", "")
        )

    lang_ins = _LANG_INSTRUCTIONS.get(lang, _LANG_INSTRUCTIONS["zh"])
    lines = [
        lang_ins[0],   # language instruction (must use XX language)
        lang_ins[1],   # role description
        "",
        "Below is an event log captured while the user worked in AutoCAD.",
        lang_ins[2],   # second-person instruction
        "",
        "Guidelines:",
        "- Group related commands into logical tutorial steps",
        "- Use official AutoCAD command names (e.g., EXTRUDE, FILLET, DIMLINEAR)",
        "- Explain *why* each step matters, not just what was clicked",
        "- For 3D operations, mention the effect on the model",
        "- For layer/block operations, explain the organisational benefit",
        "- Keep each step 1-3 sentences; be concise",
        "- If voice notes are present, refine them into professional prose",
        "- End with a brief summary of what was accomplished",
        "- Output only the narration script, no meta-commentary",
        "",
        "═══ AUTOCAD SESSION LOG ═══",
        "",
    ]

    # Command summary by category
    if cat_counts:
        lines.append("── COMMAND SUMMARY BY CATEGORY ──")
        for cat, cmds in cat_counts.items():
            label = _CATEGORY_LABELS.get(cat, cat)
            unique = list(dict.fromkeys(cmds))[:10]
            lines.append(f"  {label}: {', '.join(unique)}")
        lines.append("")

    # Full command timeline
    if cmd_events:
        lines.append("── COMMAND TIMELINE ──")
        begin_cmds = [e for e in cmd_events if "BeginCommand" in (e.get("uia_automation_id") or "")]
        for e in begin_cmds[:60]:
            name = e.get("uia_element_name", "")
            typ  = e.get("uia_element_type", "")
            cat  = typ.split(":")[-1] if ":" in typ else ""
            ts   = e.get("timestamp", "")
            lines.append(f"  [{ts}] {name}  ({_CATEGORY_LABELS.get(cat, cat)})")
        lines.append("")

    # Object additions (entity types created)
    if obj_events:
        lines.append("── ENTITIES CREATED ──")
        entity_counts: dict[str, int] = {}
        for e in obj_events:
            ent = e.get("uia_element_name", "Entity")
            entity_counts[ent] = entity_counts.get(ent, 0) + 1
        for ent, cnt in sorted(entity_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {ent}: ×{cnt}")
        lines.append("")

    # Window/layout changes
    if window_evts:
        lines.append("── WINDOW / LAYOUT CHANGES ──")
        for e in window_evts[:20]:
            lines.append(f"  [{e.get('timestamp','')}] {e.get('window_title','')}")
        lines.append("")

    # Voice notes
    if voice_segs:
        lines.append("── VOICE NOTES (raw) ──")
        for e in voice_segs:
            lines.append(f"  [{e.get('timestamp','')}] {e.get('voice_text','')}")
        lines.append("")

    if screenshots:
        lines.append(f"── SCREENSHOTS ── ({len(screenshots)} captured)")
        lines.append("")

    lines.append("═══ END OF LOG ═══")
    lines.append("")
    lines.append("Write the narration script now:")

    return "\n".join(lines)


def _generate_narration_sync(events: List[dict], lang: str = "zh") -> str:
    """Call Azure OpenAI GPT synchronously (runs in a thread)."""
    if not _GPT:
        logger.warning("GPTAssistant not available — using fallback narration")
        return _fallback_narration(events)

    api_key = (os.environ.get("AZURE_OPENAI_API_KEY")
               or os.environ.get("OPENAI_API_KEY", ""))
    if not api_key:
        logger.warning("AZURE_OPENAI_API_KEY not set — using fallback narration")
        return _fallback_narration(events)

    try:
        gpt = GPTAssistant()
        prompt = _build_autocad_prompt(events, lang=lang)
        result = gpt.chat(prompt)
        if result:
            return result
        logger.warning("GPT returned empty response — using fallback narration")
        return _fallback_narration(events)
    except Exception as exc:
        logger.error("GPT narration failed: %s", exc)
        return _fallback_narration(events)


def _fallback_narration(events: List[dict]) -> str:
    cmds = [e for e in events if "BeginCommand" in (e.get("uia_automation_id") or "")]
    voice = [e for e in events if e["event_type"] == "voice_segment"]
    lines = ["[AutoCAD Tutorial — Auto-generated narration]\n"]
    if voice:
        lines.append("Voice notes:")
        for s in voice:
            lines.append(f"  • {s.get('voice_text','')}")
        lines.append("")
    if cmds:
        lines.append("Commands executed:")
        for e in cmds[:30]:
            lines.append(f"  • {e.get('uia_element_name','')}")
    if not cmds and not voice:
        lines.append("No commands captured. Ensure AutoCAD is running and pywin32 is installed.")
    return "\n".join(lines)


# ── AutoCADScribeAgent ────────────────────────────────────────────────────────

class AutoCADScribeAgent:
    """
    Orchestrates AutoCAD COM monitor + voice + screenshots for a scribe session.
    Uses the same scribe_sessions / scribe_events DB tables as ScribeAgent.
    """

    def __init__(self) -> None:
        self._lock      = threading.Lock()
        self._running   = False
        self._session_id: Optional[int]              = None
        self._seq       = 0
        self._monitor:  Optional[AutoCADMonitor]     = None
        self._voice:    Optional[VoiceCapture]       = None
        self._periodic: Optional[threading.Thread]   = None
        self._writer:   Optional[threading.Thread]   = None
        self._conn:     Optional[sqlite3.Connection] = None
        self._screenshot_folder: Optional[Path]      = None
        self._screenshot_interval = 30
        self._screenshot_on_command = True   # take screenshot after each command
        self._last_cmd_screenshot: float = 0.0  # monotonic time of last command screenshot
        self._cmd_screenshot_cooldown = 2.0  # minimum seconds between command screenshots
        # Async write queue — events are queued here and flushed every 500 ms
        # by a dedicated writer thread, so event callbacks never block on I/O.
        self._write_queue: queue.Queue = queue.Queue()

    # ── public ───────────────────────────────────────────────────────────

    def start(
        self,
        title:                  str  = "",
        screenshot_interval:    int  = 30,
        enable_voice:           bool = True,
        enable_com:             bool = True,
        screenshot_on_command:  bool = True,
    ) -> int:
        with self._lock:
            if self._running:
                raise RuntimeError("AutoCAD scribe session already in progress")
            self._screenshot_interval   = screenshot_interval
            self._screenshot_on_command = screenshot_on_command
            self._last_cmd_screenshot   = 0.0

        from app.database import DB_PATH
        self._conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        now = _utcnow()
        cur = self._conn.execute(
            """INSERT INTO scribe_sessions
               (title, target_app, started_at, status, screenshot_dir)
               VALUES (?,?,?,?,?)""",
            (title, "acad.exe", now, "recording", ""),
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

        # Start the async DB writer first, then the event sources
        self._writer = threading.Thread(
            target=self._writer_loop, name="AutoCAD-writer", daemon=True
        )
        self._writer.start()

        self._save_screenshot("session_start")

        if enable_com:
            self._monitor = AutoCADMonitor()
            self._monitor.start(self._on_event)

        if enable_voice:
            audio_dir = SCREENSHOTS_BASE / str(sid) / "audio"
            self._voice = VoiceCapture(audio_dir=audio_dir)
            self._voice.start(self._on_voice_segment)

        self._periodic = threading.Thread(
            target=self._periodic_loop, name="AutoCAD-periodic", daemon=True
        )
        self._periodic.start()

        logger.info("AutoCADScribeAgent started session_id=%d", sid)
        return sid

    def stop(self, generate_narration: bool = True, lang: str = "zh") -> Optional[int]:
        with self._lock:
            if not self._running:
                return None
            self._running = False
            sid = self._session_id

        if self._monitor:
            self._monitor.stop()
            self._monitor = None

        if self._voice:
            self._voice.stop()
            self._voice = None

        if self._periodic:
            self._periodic.join(timeout=5)

        # Flush remaining writes before closing
        self._write_queue.put(None)   # sentinel
        if self._writer:
            self._writer.join(timeout=5)

        if self._conn:
            self._conn.execute(
                "UPDATE scribe_sessions SET ended_at=?, status=? WHERE id=?",
                (_utcnow(), "processing" if generate_narration else "done", sid),
            )
            self._conn.commit()

        if generate_narration and sid is not None:
            self._run_narration(sid, lang=lang)

        if self._conn:
            self._conn.close()
            self._conn = None

        self._session_id = None
        logger.info("AutoCADScribeAgent stopped session_id=%d", sid)
        return sid

    @property
    def status(self) -> dict:
        with self._lock:
            return {
                "running":        self._running,
                "session_id":     self._session_id,
                "events_captured": self._seq,
                "voice_segments": len(self._voice.drain()) if self._voice else 0,
                "uia_events":     0,
            }

    # ── narration ─────────────────────────────────────────────────────────

    def _run_narration(self, sid: int, lang: str = "zh") -> None:
        from app.database import DB_PATH
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT event_type, timestamp, app_name, window_title,
                          uia_element_name, uia_element_type, uia_automation_id,
                          screenshot_path, voice_text, voice_confidence
                   FROM scribe_events WHERE session_id=? ORDER BY seq""",
                (sid,),
            ).fetchall()
            conn.close()

            events = [dict(r) for r in rows]
            narration = _generate_narration_sync(events, lang=lang)

            conn2 = sqlite3.connect(str(DB_PATH))
            conn2.execute(
                "UPDATE scribe_sessions SET narration_text=?, status='done' WHERE id=?",
                (narration, sid),
            )
            conn2.commit()
            conn2.close()
            logger.info("AutoCAD narration done for session %d (%d chars)", sid, len(narration))

        except Exception as exc:
            logger.error("AutoCAD narration failed for session %d: %s", sid, exc)
            try:
                from app.database import DB_PATH as DP
                c = sqlite3.connect(str(DP))
                c.execute(
                    "UPDATE scribe_sessions SET status='error', error_message=? WHERE id=?",
                    (str(exc), sid),
                )
                c.commit(); c.close()
            except Exception:
                pass

    # ── event handlers ────────────────────────────────────────────────────

    def _on_event(self, event: dict) -> None:
        with self._lock:
            if not self._running:
                return
            rid = self._session_id
            on_cmd = self._screenshot_on_command
        self._write_event(
            session_id=rid,
            event_type=event.get("event_type", "uia_invoke"),
            app_name=event.get("app_name", "acad.exe"),
            window_title=event.get("window_title"),
            uia_element_name=event.get("uia_element_name"),
            uia_element_type=event.get("uia_element_type"),
            uia_automation_id=event.get("uia_automation_id"),
        )

        # Take a screenshot when a command finishes (EndCommand).
        # Cooldown prevents screenshot floods on rapid commands like UNDO/REDO.
        if on_cmd:
            aid = event.get("uia_automation_id") or ""
            if aid.startswith("EndCommand:"):
                now = time.monotonic()
                with self._lock:
                    elapsed = now - self._last_cmd_screenshot
                    if elapsed >= self._cmd_screenshot_cooldown:
                        self._last_cmd_screenshot = now
                        do_shot = True
                    else:
                        do_shot = False
                if do_shot:
                    cmd_name = aid[len("EndCommand:"):]
                    self._save_screenshot(f"cmd:{cmd_name}")

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
        """Queue an event for async batch write — never blocks the caller."""
        kwargs.setdefault("timestamp", _utcnow())
        kwargs["seq"] = self._next_seq()
        self._write_queue.put(kwargs)

    def _writer_loop(self) -> None:
        """
        Background thread: drain the write queue every 500 ms in a single
        transaction.  This replaces per-event commits and eliminates the I/O
        bottleneck that caused lag during high-frequency operations.
        """
        FLUSH_INTERVAL = 0.5   # seconds between flushes

        while True:
            time.sleep(FLUSH_INTERVAL)

            # Drain everything currently in the queue
            batch = []
            while True:
                try:
                    item = self._write_queue.get_nowait()
                except queue.Empty:
                    break
                if item is None:        # stop sentinel
                    self._flush_batch(batch)
                    return
                batch.append(item)

            self._flush_batch(batch)

    def _flush_batch(self, batch: list) -> None:
        if not batch or not self._conn:
            return
        try:
            for kwargs in batch:
                cols = ", ".join(kwargs.keys())
                ph   = ", ".join("?" * len(kwargs))
                self._conn.execute(
                    f"INSERT INTO scribe_events ({cols}) VALUES ({ph})",
                    list(kwargs.values()),
                )
            self._conn.commit()          # one commit for the whole batch
        except Exception as exc:
            logger.debug("batch write failed: %s", exc)

    def _save_screenshot(self, trigger: str = "periodic") -> None:
        """Take screenshot in a background thread so it never blocks event flow."""
        if not self._screenshot_folder:
            return
        folder = self._screenshot_folder
        with self._lock:
            rid = self._session_id

        def _do():
            ts  = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            rel = _take_screenshot(folder, f"{ts}.jpg")
            if rel:
                self._write_event(
                    session_id=rid,
                    event_type="screenshot",
                    screenshot_path=rel,
                    annotation=trigger,
                )

        threading.Thread(target=_do, daemon=True).start()

    def _periodic_loop(self) -> None:
        while True:
            time.sleep(self._screenshot_interval)
            with self._lock:
                if not self._running:
                    break
            self._save_screenshot("periodic")


# Module-level singleton
autocad_agent = AutoCADScribeAgent()