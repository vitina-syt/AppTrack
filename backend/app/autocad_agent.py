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
import json
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
    from pynput import mouse as _mouse_lib
    from pynput import keyboard as _kb_lib
    _PYNPUT = True
except ImportError:
    _PYNPUT = False

try:
    import win32gui as _win32gui
    import win32process as _win32process
    import psutil as _psutil
    _WIN32_AVAIL = True
except ImportError:
    _WIN32_AVAIL = False

# ── WinRT OCR — Windows 10/11 built-in OCR engine ─────────────────────────
# Install: pip install winsdk
_WINSDK_OCR = False
try:
    import winsdk.windows.media.ocr           as _wocr
    import winsdk.windows.storage.streams     as _wstreams
    import winsdk.windows.graphics.imaging    as _wimaging
    _WINSDK_OCR = True
except ImportError:
    pass

# ── UIAutomation — for reading element names at click position ─────────────
_uia_instance = None
try:
    import ctypes.wintypes as _wintypes
    import comtypes as _comtypes
    import comtypes.client as _comtypes_client
    _comtypes_client.GetModule("UIAutomationCore.dll")
    import comtypes.gen.UIAutomationClient as _UIAC
    _comtypes.CoInitializeEx(_comtypes.COINIT_MULTITHREADED)
    _uia_instance = _comtypes_client.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}",
        interface=_UIAC.IUIAutomation,
    )
except Exception as _uia_err:
    logger.info("UIA not available for click labels (%s)", _uia_err)


def _get_element_name_at_point(x: int, y: int) -> str:
    """Return the UIA element name (e.g. toolbar button label) at screen coords."""
    if _uia_instance is None:
        return ""
    try:
        pt = _wintypes.POINT(int(x), int(y))
        el = _uia_instance.ElementFromPoint(pt)
        name = (el.CurrentName or "").strip()
        if not name:
            name = (el.CurrentLocalizedControlType or "").strip()
        return name
    except Exception:
        return ""


def _scan_tooltip_windows() -> str:
    """Scan all windows (top-level + owned) for any visible tooltip text."""
    if not _WIN32_AVAIL:
        return ""
    results = []

    def _check(hwnd):
        try:
            if not _win32gui.IsWindowVisible(hwnd):
                return
            cls = _win32gui.GetClassName(hwnd)
            if "tooltip" in cls.lower() or cls in ("tooltips_class32", "SynsTooltips"):
                txt = _win32gui.GetWindowText(hwnd).strip()
                if txt:
                    results.append(txt)
        except Exception:
            pass

    try:
        _win32gui.EnumWindows(lambda h, _: _check(h), None)
    except Exception:
        pass
    return results[0] if results else ""


class _TooltipPoller:
    """Background thread that polls tooltip windows every 150 ms and caches the last text.

    Creo's tooltip disappears the moment the mouse button is pressed, so reading it
    at click-time is always too late.  Continuous polling + caching solves the timing.
    """

    def __init__(self) -> None:
        self._text      = ""
        self._ts        = 0.0
        self._lock      = threading.Lock()
        self._running   = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._running = True
        self._thread  = threading.Thread(target=self._run, daemon=True,
                                         name="TooltipPoller")
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def get(self, max_age: float = 2.0) -> str:
        """Return cached tooltip text if seen within *max_age* seconds."""
        with self._lock:
            return self._text if (time.monotonic() - self._ts) < max_age else ""

    def _run(self) -> None:
        while self._running:
            txt = _scan_tooltip_windows()
            if txt:
                with self._lock:
                    self._text = txt
                    self._ts   = time.monotonic()
            time.sleep(0.15)


import re as _re

_CREO_CMD_PREFIXES = ("ProCmd", "ProCrt", "Cmd")
_CREO_CMD_SUFFIXES = ("UI", "Dlg", "Cmd")

def _parse_creo_cmd_name(raw: str) -> str:
    """Convert a Creo internal command ID to a human-readable label.

    Examples:
      ProCmdModelDisplay     → "Model Display"
      ProCmdSysAppearance    → "Sys Appearance"
      FastLoadModel          → "Fast Load Model"
      ModelDisplayUI         → "Model Display"
    """
    s = raw.strip()
    for pfx in _CREO_CMD_PREFIXES:
        if s.startswith(pfx):
            s = s[len(pfx):]
            break
    for sfx in _CREO_CMD_SUFFIXES:
        if s.endswith(sfx):
            s = s[: -len(sfx)]
            break
    # Split PascalCase / camelCase into words
    words = _re.sub(r"([A-Z][a-z])", r" \1", _re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)).strip()
    return words or raw


def _find_creo_trail_file(target_exe: str, manual_path: str = "") -> Optional[Path]:
    """Locate trail.txt.

    Search order:
      1. manual_path argument (passed from frontend UI)
      2. Environment variable  CREO_TRAIL_FILE
      3. OS working directory of the running Creo process
      4. Common user-level default locations
      5. One level deep under the user home directory

    The path can be found inside Creo via:
        File → Manage Session → Set Working Directory  (trail.txt lives there)
    """
    # ── 1. Explicit path from frontend / caller ───────────────────────────────
    manual = manual_path.strip()
    if not manual:
        manual = os.environ.get("CREO_TRAIL_FILE", "").strip()
    if manual:
        p = Path(manual)
        if p.exists():
            logger.info("Using CREO_TRAIL_FILE env var: %s", p)
            return p
        logger.warning("CREO_TRAIL_FILE set to %r but file does not exist", manual)

    candidates: list[Path] = []

    # ── 2. OS working directory of every matching process ─────────────────────
    if _WIN32_AVAIL:
        exe_lower = target_exe.lower()
        for proc in _psutil.process_iter(["name", "pid"]):
            try:
                if (proc.info["name"] or "").lower() == exe_lower:
                    cwd = Path(proc.cwd())
                    logger.info("Creo process CWD: %s", cwd)
                    candidates.append(cwd / "trail.txt")
            except (_psutil.NoSuchProcess, _psutil.AccessDenied):
                pass
            except Exception as exc:
                logger.debug("CWD read failed: %s", exc)

    # ── 3. Common user-level defaults ─────────────────────────────────────────
    home = Path(os.path.expanduser("~"))
    candidates += [
        home / "trail.txt",
        home / "Documents" / "trail.txt",
        Path(os.getcwd()) / "trail.txt",
    ]

    # ── 4. One level deep in home ─────────────────────────────────────────────
    try:
        for p in home.iterdir():
            if p.is_dir():
                candidates.append(p / "trail.txt")
    except Exception:
        pass

    for path in candidates:
        try:
            if path.exists():
                logger.info("Auto-detected Creo trail file: %s", path)
                logger.info(
                    "Tip: set CREO_TRAIL_FILE=%s to skip auto-detection next time", path
                )
                return path
        except Exception:
            pass

    logger.warning(
        "Creo trail.txt not found automatically.\n"
        "  → In Creo: File → Manage Session → Set Working Directory\n"
        "  → Then set env var:  CREO_TRAIL_FILE=<that directory>\\trail.txt\n"
        "  Checked locations: %s",
        [str(c) for c in candidates[:8]],
    )
    return None


class _CreoTrailMonitor:
    """Tail Creo's trail.txt and expose the most recent activated command name."""

    def __init__(self, trail_path: Path) -> None:
        self._path       = trail_path
        self._last_cmd   = ""
        self._last_ts    = 0.0
        self._lock       = threading.Lock()
        self._running    = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._running = True
        self._thread  = threading.Thread(target=self._run, daemon=True,
                                         name="CreoTrail-monitor")
        self._thread.start()
        logger.info("Creo trail monitor started: %s", self._path)

    def stop(self) -> None:
        self._running = False

    def get_recent_cmd(self, max_age: float = 1.5) -> str:
        """Return the last command name if it was seen within *max_age* seconds."""
        with self._lock:
            age = time.monotonic() - self._last_ts
            return self._last_cmd if age < max_age else ""

    def _run(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(0, 2)          # start at end — only watch new lines
                while self._running:
                    line = f.readline()
                    if not line:
                        time.sleep(0.05)
                        continue
                    cmd = self._parse(line)
                    if cmd:
                        with self._lock:
                            self._last_cmd = cmd
                            self._last_ts  = time.monotonic()
                        logger.debug("Creo trail cmd: %r", cmd)
        except Exception as exc:
            logger.warning("Creo trail monitor error: %s", exc)

    @staticmethod
    def _parse(line: str) -> str:
        """Parse '~ Activate `ctx` `CmdName`' → human-readable label."""
        line = line.strip()
        if not line.startswith("~ Activate"):
            return ""
        parts = line.split("`")
        # Format: ~ Activate `context` `CommandName`
        if len(parts) >= 4:
            return _parse_creo_cmd_name(parts[-2].strip())
        return ""


def _is_target_foreground(target_exe: str) -> bool:
    """Return True if *target_exe* is currently the foreground window."""
    if not _WIN32_AVAIL:
        return True  # cannot check, assume yes
    try:
        hwnd = _win32gui.GetForegroundWindow()
        _, pid = _win32process.GetWindowThreadProcessId(hwnd)
        name = _psutil.Process(pid).name()
        return name.lower() == target_exe.lower()
    except Exception:
        return False


def is_target_running(target_exe: str) -> bool:
    """Return True if a process named *target_exe* is currently running."""
    if not _WIN32_AVAIL:
        return True  # cannot check, assume yes so we don't block non-Windows dev
    try:
        target = target_exe.lower()
        for proc in _psutil.process_iter(["name"]):
            try:
                if (proc.info["name"] or "").lower() == target:
                    return True
            except (_psutil.NoSuchProcess, _psutil.AccessDenied):
                pass
        return False
    except Exception:
        return True  # if psutil fails, don't block the start


def get_running_windows() -> list:
    """
    Return a list of {exe, title, pid} for every visible, titled window.
    De-duplicated by pid so each process appears at most once.
    Falls back to an empty list on non-Windows / missing deps.
    """
    if not _WIN32_AVAIL:
        return []
    results = []
    seen_pids: set = set()

    def _cb(hwnd, _):
        if not _win32gui.IsWindowVisible(hwnd):
            return
        title = _win32gui.GetWindowText(hwnd)
        if not title:
            return
        try:
            _, pid = _win32process.GetWindowThreadProcessId(hwnd)
            if pid in seen_pids:
                return
            seen_pids.add(pid)
            proc = _psutil.Process(pid)
            results.append({
                "exe":   proc.name(),
                "title": title,
                "pid":   pid,
            })
        except (_psutil.NoSuchProcess, _psutil.AccessDenied, Exception):
            pass

    try:
        _win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    return sorted(results, key=lambda x: x["exe"].lower())

try:
    from app.gpt_assistant import GPTAssistant
    _GPT = True
except Exception:
    _GPT = False

from app.autocad_monitor import AutoCADMonitor, categorize_command, COMMAND_CATEGORIES
from app.voice_capture import VoiceCapture

from app.database import DATA_DIR
import sys as _sys
if getattr(_sys, "frozen", False):
    SCREENSHOTS_BASE = Path("C:/document/AppTrack/autocad_screenshots")
else:
    SCREENSHOTS_BASE = DATA_DIR / "autocad_screenshots"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_window_rect_for_exe(target_exe: str) -> Optional[dict]:
    """
    Return an mss-compatible monitor dict {left, top, width, height} for the
    first visible, non-minimised window belonging to *target_exe*, or None.
    """
    if not _WIN32_AVAIL:
        return None
    target = target_exe.lower()
    found = []

    def _cb(hwnd, _):
        if not _win32gui.IsWindowVisible(hwnd):
            return
        # SW_SHOWMINIMIZED = 2 — skip minimised windows
        if _win32gui.GetWindowPlacement(hwnd)[1] == 2:
            return
        try:
            _, pid = _win32process.GetWindowThreadProcessId(hwnd)
            if _psutil.Process(pid).name().lower() != target:
                return
            left, top, right, bottom = _win32gui.GetWindowRect(hwnd)
            w, h = right - left, bottom - top
            if w > 0 and h > 0:
                found.append({"left": left, "top": top, "width": w, "height": h})
        except Exception:
            pass

    try:
        _win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    if not found:
        return None
    # Return the window with the largest area — that's the main app window,
    # not a toolbar or popup belonging to the same process.
    return max(found, key=lambda r: r["width"] * r["height"])


def _take_screenshot(
    folder: Path,
    filename: str,
    target_exe: Optional[str] = None,
) -> Optional[str]:
    if not _MSS:
        return None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / filename
        with mss.mss() as sct:
            if target_exe:
                region = _get_window_rect_for_exe(target_exe)
                raw = sct.grab(region) if region else sct.grab(sct.monitors[0])
            else:
                raw = sct.grab(sct.monitors[0])
        if _PIL:
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            img.save(str(path), format="JPEG", quality=92, subsampling=0)
        else:
            png = path.with_suffix(".png")
            mss.tools.to_png(raw.rgb, raw.size, output=str(png))
            return png.name
        return path.name
    except Exception as exc:
        logger.error("Screenshot failed (folder=%s, file=%s): %s", folder, filename, exc, exc_info=True)
        return None


def _transcribe_pcm(raw: bytes) -> tuple:
    """Transcribe raw PCM bytes (16 kHz, mono, int16) via Azure OpenAI Whisper.

    Returns (text, confidence).  Used to attach per-frame voice to screenshots.
    """
    if not raw:
        return "", 0.0
    import tempfile
    import wave as _wave
    from app.voice_capture import _transcribe_file, _normalize_audio
    raw = _normalize_audio(raw)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = f.name
    try:
        with _wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(raw)
        return _transcribe_file(wav_path)
    except Exception as exc:
        logger.warning("per-frame PCM transcription failed: %s", exc)
        return "", 0.0
    finally:
        try:
            Path(wav_path).unlink(missing_ok=True)
        except Exception:
            pass


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


def _is_shift_held() -> bool:
    """Return True if the Shift key is currently held down (Windows only)."""
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000)
    except Exception:
        return False


def _ocr_at_point(x: int, y: int, w: int = 160, h: int = 60) -> str:
    """
    Capture a w×h pixel region centred on (x, y) and run Windows built-in
    OCR (Windows.Media.Ocr) on it.

    Used as a last-resort label source when UIA / tooltip / trail file all
    return nothing — most useful for Creo's ribbon buttons whose labels are
    painted directly on screen rather than exposed via accessibility APIs.

    Requirements
    ------------
    pip install winsdk pillow
    The WinRT OCR engine is included in every Windows 10/11 install and
    supports the system language pack; no API key needed.
    """
    if not _WINSDK_OCR or not _PIL:
        return ""
    try:
        import asyncio, io
        from PIL import ImageGrab

        left = max(0, x - w // 2)
        top  = max(0, y - h // 2)
        img  = ImageGrab.grab(bbox=(left, top, left + w, top + h))
        # 2× upscale — noticeably improves accuracy for small UI text
        img  = img.resize((img.width * 2, img.height * 2))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png = buf.getvalue()

        async def _run() -> str:
            stream = _wstreams.InMemoryRandomAccessStream()
            writer = _wstreams.DataWriter(stream)
            writer.write_bytes(bytearray(png))
            await writer.store_async()
            writer.detach_stream()
            stream.seek(0)

            decoder = await _wimaging.BitmapDecoder.create_async(stream)
            bitmap  = await decoder.get_software_bitmap_async()

            engine = _wocr.OcrEngine.try_create_from_user_profile_languages()
            if not engine:
                return ""
            result = await engine.recognize_async(bitmap)
            return (result.text or "").strip() if result else ""

        loop = asyncio.new_event_loop()
        try:
            text = loop.run_until_complete(_run())
        finally:
            loop.close()

        # Collapse OCR-introduced line breaks / extra spaces between chars
        text = " ".join(text.split())
        logger.debug("WinRT OCR at (%d,%d) → %r", x, y, text)
        return text

    except Exception as exc:
        logger.debug("WinRT OCR error at (%d,%d): %s", x, y, exc)
        return ""


def _build_autocad_prompt(events: List[dict], lang: str = "zh", background: str = "") -> str:
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
        *([f"Background context provided by the instructor: {background}", ""]
          if background.strip() else []),
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


def _get_click_label(event: dict) -> str:
    """Extract the UI element label from a screenshot event's shapes_json.

    The label is stored as the ``text`` field of the click_circle shape that
    was auto-inserted into frame_annotations when the screenshot was taken.
    It is NOT the same as scribe_events.annotation (which is the trigger type).
    """
    shapes_raw = event.get("shapes_json")
    if not shapes_raw:
        return ""
    try:
        for shape in json.loads(shapes_raw):
            if shape.get("type") == "click_circle":
                return (shape.get("text") or "").strip()
    except Exception:
        pass
    return ""


def _build_creo_prompt(events: List[dict], lang: str = "zh", background: str = "") -> str:
    """Build a Creo Parametric-specific prompt from captured events.

    Uses a unified chronological timeline that merges click labels
    (from frame_annotations.shapes_json), UIA invocations, voice notes,
    and view-change screenshots into a single ordered log.
    """
    lang_ins = _LANG_INSTRUCTIONS.get(lang, _LANG_INSTRUCTIONS["zh"])

    # ── Build unified chronological timeline ──────────────────────────────────
    timeline: list[tuple[str, str]] = []
    for e in events:
        et = e.get("event_type", "")
        ts = (e.get("timestamp") or "")

        if et == "screenshot":
            trigger     = e.get("annotation") or ""
            trig_label  = ScreenshotTrigger.label(trigger)
            click_label = _get_click_label(e)

            if trigger in _VIEW_TRIGGERS:
                # View adjustments: include as context, briefly
                timeline.append((ts, f"[视角] {trig_label}"))
            elif click_label:
                # Click with identified UI element — primary signal
                timeline.append((ts, f"[点击] {trig_label}: {click_label}"))
            # Clicks with no resolved label are omitted (not informative)

        elif et == "uia_invoke":
            name = (e.get("uia_element_name") or "").strip()
            if name:
                timeline.append((ts, f"[操作] {name}"))

        elif et == "voice_segment":
            text = (e.get("voice_text") or "").strip()
            if text:
                timeline.append((ts, f"[语音] {text}"))

    # ── Build prompt ─────────────────────────────────────────────────────────
    lines = [
        lang_ins[0],
        "You are a senior mechanical-design instructor writing a professional "
        "step-by-step narration script for a PTC Creo Parametric tutorial video.",
        "",
        "Below is a chronological operation log captured while the user worked in Creo.",
        lang_ins[2],
        "",
        *([f"Background context provided by the instructor: {background}", ""]
          if background.strip() else []),
        "Log entry prefixes and their meaning:",
        "  [点击] — mouse click with the name of the clicked UI element  ← primary signal",
        "  [操作] — UI button / menu item invoked via keyboard shortcut    ← secondary signal",
        "  [视角] — view adjustment (rotate / zoom / pan)                  ← context only",
        "  [语音] — user's own voice note, reveals design intent           ← weave in naturally",
        "",
        "Writing guidelines:",
        "- Use official Creo feature names: Extrude, Revolve, Sweep, Pattern, Round, "
        "Chamfer, Shell, Draft, Rib, Hole, Mirror, Sketch, Datum Plane, Mate, etc.",
        "- Narrate [点击] and [操作] entries in sequence as the main story",
        "- Refine [语音] notes into professional prose, preserving the design intent",
        "- Mention [视角] briefly ('rotate to inspect the result') or skip them entirely",
        "- Explain *why* each step matters in the overall design workflow",
        "- Keep each step 1–3 sentences; be concise",
        "- End with a one-sentence summary of what was accomplished",
        "- Output only the narration script — no section headers, no meta-commentary",
        "",
        "═══ CREO SESSION LOG ═══",
        "",
    ]

    if timeline:
        for ts, desc in timeline:
            lines.append(f"  [{ts}] {desc}")
    else:
        lines.append("  (no events captured)")
    lines.append("")

    lines.append("═══ END OF LOG ═══")
    lines.append("")
    lines.append("Write the narration script now:")
    return "\n".join(lines)


def _build_generic_prompt(events: List[dict], target_exe: str, lang: str = "zh",
                          background: str = "") -> str:
    """Fallback prompt for apps with no dedicated builder."""
    voice_segs  = [e for e in events if e["event_type"] == "voice_segment"]
    screenshots = [e for e in events if e["event_type"] == "screenshot"]
    invokes     = [e for e in events if e["event_type"] == "uia_invoke"
                   and (e.get("uia_element_name") or "").strip()]

    app_name = target_exe.replace(".exe", "").capitalize()
    lang_ins = _LANG_INSTRUCTIONS.get(lang, _LANG_INSTRUCTIONS["zh"])
    lines = [
        lang_ins[0],
        f"You are creating a professional narration script for a {app_name} tutorial video.",
        "",
        f"Below is an interaction log captured while the user worked in {app_name}.",
        lang_ins[2],
        "",
        *([f"Background context provided by the instructor: {background}", ""]
          if background.strip() else []),
        "Guidelines:",
        "- Describe each operation clearly and explain its purpose",
        "- Keep each step 1-3 sentences; be concise",
        "- If voice notes are present, refine them into professional prose",
        "- End with a brief summary of what was accomplished",
        "- Output only the narration script, no meta-commentary",
        "",
        f"═══ {app_name.upper()} SESSION LOG ═══",
        "",
    ]
    if invokes:
        lines.append("── UI OPERATIONS ──")
        for e in invokes[:80]:
            lines.append(f"  [{e.get('timestamp','')}] {e.get('uia_element_name','')}  ({e.get('uia_element_type','')})")
        lines.append("")
    if voice_segs:
        lines.append("── VOICE NOTES (raw) ──")
        for e in voice_segs:
            lines.append(f"  [{e.get('timestamp','')}] {e.get('voice_text','')}")
        lines.append("")
    if screenshots:
        lines.append(f"── SCREENSHOTS ── ({len(screenshots)} captured)")
        for e in screenshots:
            label = (e.get("annotation") or "").replace("click:", "").replace("cmd:", "")
            if label:
                lines.append(f"  [{e.get('timestamp','')}] clicked: {label}")
        lines.append("")
    lines.append("═══ END OF LOG ═══")
    lines.append("")
    lines.append("Write the narration script now:")
    return "\n".join(lines)


# Known Creo executable names
_CREO_EXES = {"xtop.exe", "creo_parametric.exe", "creo.exe", "proe.exe"}


class ScreenshotTrigger:
    """
    Centralized registry of screenshot trigger identifiers.

    The trigger string is stored as the ``annotation`` field in scribe_events,
    so it surfaces in the editor filmstrip and in the AI narration prompt.

    Naming convention
    -----------------
    Simple:        lowercase_snake          e.g. "periodic"
    Parameterised: category:detail          e.g. "cmd:EXTRUDE"

    To add a new trigger type:
      1. Add a constant here.
      2. Add a Chinese label to LABELS.
      3. Call ``_save_screenshot(ScreenshotTrigger.NEW_TRIGGER, ...)`` at the
         appropriate place in AutoCADScribeAgent.
    """

    # ── General ───────────────────────────────────────────────────────────────
    PERIODIC        = "periodic"            # background timer (every N seconds)

    # ── AutoCAD ───────────────────────────────────────────────────────────────
    CMD             = "cmd"                 # prefix; full value: "cmd:{CommandName}"

    # ── Creo — mouse interactions ─────────────────────────────────────────────
    CLICK_LEFT       = "click:left"          # left-button press
    CLICK_RIGHT      = "click:right"         # right-button press
    ROTATE_LEFT      = "middle_drag:rotate_left"   # middle-button drag left  → rotate left
    ROTATE_RIGHT     = "middle_drag:rotate_right"  # middle-button drag right → rotate right
    ROTATE_UP        = "middle_drag:rotate_up"     # middle-button drag up    → rotate up
    ROTATE_DOWN      = "middle_drag:rotate_down"   # middle-button drag down  → rotate down
    SCROLL_ZOOM_IN   = "scroll:zoom_in"      # scroll down → zoom in
    SCROLL_ZOOM_OUT  = "scroll:zoom_out"     # scroll up   → zoom out
    SHIFT_PAN_LEFT   = "shift_middle:pan_left"   # Shift + middle drag → pan left
    SHIFT_PAN_RIGHT  = "shift_middle:pan_right"  # Shift + middle drag → pan right
    SHIFT_PAN_UP     = "shift_middle:pan_up"     # Shift + middle drag → pan up
    SHIFT_PAN_DOWN   = "shift_middle:pan_down"   # Shift + middle drag → pan down

    # ── Creo — planned / reserved (uncomment to activate) ─────────────────────
    # TRAIL_CMD     = "trail_cmd"           # trail-file command activation
    # KEY_SHORTCUT  = "key"                 # keyboard shortcut (e.g. Ctrl+D)

    # ── Human-readable labels (used in editor filmstrip & logs) ───────────────
    LABELS: dict = {
        "periodic":                "定时截图",
        "cmd":                     "命令截图",
        "click:left":              "左键点击",
        "click:right":             "右键点击",
        "middle_drag:rotate_left":  "中键左旋转",
        "middle_drag:rotate_right": "中键右旋转",
        "middle_drag:rotate_up":    "中键上旋转",
        "middle_drag:rotate_down":  "中键下旋转",
        "scroll:zoom_in":           "滚轮放大",
        "scroll:zoom_out":          "滚轮缩小",
        "shift_middle:pan_left":    "Shift+中键左平移",
        "shift_middle:pan_right":   "Shift+中键右平移",
        "shift_middle:pan_up":      "Shift+中键上平移",
        "shift_middle:pan_down":    "Shift+中键下平移",
    }

    @classmethod
    def label(cls, trigger: str) -> str:
        """Return a human-readable Chinese label for a trigger string.

        Handles both exact matches (``"periodic"``) and parameterised ones
        (``"cmd:EXTRUDE"`` → ``"命令截图（EXTRUDE）"``).
        """
        if trigger in cls.LABELS:
            return cls.LABELS[trigger]
        # Try prefix match for parameterised triggers
        for key, text in cls.LABELS.items():
            if trigger.startswith(key + ":"):
                detail = trigger[len(key) + 1:]
                return f"{text}（{detail}）"
        return trigger


# Trigger types that represent view adjustments (rotation / zoom / pan)
_VIEW_TRIGGERS = {
    ScreenshotTrigger.ROTATE_LEFT,
    ScreenshotTrigger.ROTATE_RIGHT,
    ScreenshotTrigger.ROTATE_UP,
    ScreenshotTrigger.ROTATE_DOWN,
    ScreenshotTrigger.SCROLL_ZOOM_IN,
    ScreenshotTrigger.SCROLL_ZOOM_OUT,
    ScreenshotTrigger.SHIFT_PAN_LEFT,
    ScreenshotTrigger.SHIFT_PAN_RIGHT,
    ScreenshotTrigger.SHIFT_PAN_UP,
    ScreenshotTrigger.SHIFT_PAN_DOWN,
}


def _build_prompt(events: List[dict], target_exe: str = "acad.exe", lang: str = "zh",
                  background: str = "") -> str:
    """Select the right prompt builder based on target application."""
    exe = target_exe.lower()
    if exe in ("acad.exe", "autocad.exe"):
        return _build_autocad_prompt(events, lang=lang, background=background)
    if exe in _CREO_EXES:
        return _build_creo_prompt(events, lang=lang, background=background)
    return _build_generic_prompt(events, target_exe=target_exe, lang=lang, background=background)


def _generate_narration_sync(events: List[dict], lang: str = "zh",
                              target_exe: str = "acad.exe",
                              background: str = "") -> str:
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
        prompt = _build_prompt(events, target_exe=target_exe, lang=lang, background=background)
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
        self._target_exe = "acad.exe"
        self._screenshot_interval = 30
        self._screenshot_on_command = True   # take screenshot after each command
        self._last_cmd_screenshot: float = 0.0  # monotonic time of last command screenshot
        self._cmd_screenshot_cooldown = 2.0  # minimum seconds between command screenshots
        self._screenshot_on_click = False    # take screenshot on each left/right click
        self._last_click_screenshot: float = 0.0
        self._click_cooldown = 1.0           # minimum seconds between click screenshots
        self._screenshot_on_middle_drag = False  # screenshot after middle-button drag (rotation)
        self._middle_pressed    = False
        self._middle_press_pos  = (0, 0)     # (x, y) where middle button went down
        self._middle_drag_threshold = 30     # pixels of movement to count as a drag
        self._last_middle_screenshot: float = 0.0
        self._middle_cooldown = 1.5
        self._screenshot_on_scroll_zoom = False  # screenshot after scroll-wheel zoom
        self._scroll_timer: Optional[threading.Timer] = None
        self._screenshot_on_shift_pan = False    # screenshot after Shift+middle drag (pan)
        self._shift_at_middle_press = False      # was Shift held when middle button went down
        self._last_shift_pan_screenshot: float = 0.0
        self._shift_pan_cooldown = 1.5
        self._click_listener = None
        self._kb_listener    = None
        self._shift_held     = False   # real-time Shift state tracked by keyboard listener
        self._last_screenshot_time: float = 0.0  # monotonic time of the last screenshot (for voice window)
        self._last_invoke_name: str  = ""    # most recent uia_invoke element name
        self._last_invoke_ts:  float = 0.0   # monotonic time of that invoke
        self._trail_monitor:   Optional[_CreoTrailMonitor] = None
        self._tooltip_poller:  Optional[_TooltipPoller]    = None
        # Async write queue — events are queued here and flushed every 500 ms
        # by a dedicated writer thread, so event callbacks never block on I/O.
        self._write_queue: queue.Queue = queue.Queue()

    # ── public ───────────────────────────────────────────────────────────

    def start(
        self,
        title:                  str  = "",
        target_exe:             str  = "acad.exe",
        screenshot_interval:    int  = 30,
        enable_voice:           bool = True,
        enable_com:             bool = True,
        screenshot_on_command:  bool = True,
        screenshot_on_click:       bool = False,
        screenshot_on_middle_drag: bool = False,
        screenshot_on_scroll_zoom: bool = False,
        screenshot_on_shift_pan:   bool = False,
        creo_trail_file:           str  = "",
        background:                str  = "",
    ) -> int:
        with self._lock:
            if self._running:
                raise RuntimeError("AutoCAD scribe session already in progress")
            self._target_exe                 = target_exe
            self._screenshot_interval        = screenshot_interval
            self._screenshot_on_command      = screenshot_on_command
            self._last_cmd_screenshot        = 0.0
            self._screenshot_on_click        = screenshot_on_click
            self._last_click_screenshot      = 0.0
            self._screenshot_on_middle_drag  = screenshot_on_middle_drag
            self._middle_pressed             = False
            self._last_middle_screenshot     = 0.0
            self._screenshot_on_scroll_zoom  = screenshot_on_scroll_zoom
            self._screenshot_on_shift_pan    = screenshot_on_shift_pan
            self._last_shift_pan_screenshot  = 0.0

        from app.database import DB_PATH
        self._conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        now = _utcnow()
        cur = self._conn.execute(
            """INSERT INTO scribe_sessions
               (title, background, target_app, started_at, status, screenshot_dir)
               VALUES (?,?,?,?,?,?)""",
            (title, background, target_exe, now, "recording", ""),
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

        if enable_com:
            if target_exe.lower() in _CREO_EXES:
                from app.creo_uia import CreoUiaMonitor
                self._monitor = CreoUiaMonitor(target_app=target_exe)
                # Tooltip poller — caches tooltip text continuously so click-time
                # reads don't miss it (tooltip disappears on mouse-down)
                self._tooltip_poller = _TooltipPoller()
                self._tooltip_poller.start()
                # Trail file monitor — most reliable for Creo ribbon commands
                trail_path = _find_creo_trail_file(target_exe,
                                                    manual_path=creo_trail_file)
                if trail_path:
                    self._trail_monitor = _CreoTrailMonitor(trail_path)
                    self._trail_monitor.start()
                else:
                    logger.warning("Creo trail.txt not found — trail-file labels disabled")
            else:
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

        # Take an immediate first screenshot so the initial screen state is always captured,
        # regardless of how long the first periodic interval takes.
        self._save_screenshot(ScreenshotTrigger.PERIODIC)

        if screenshot_on_click or screenshot_on_middle_drag or screenshot_on_scroll_zoom or screenshot_on_shift_pan:
            if _PYNPUT:
                self._start_click_listener()
            else:
                logger.warning(
                    "Mouse listener requested but pynput is not installed — "
                    "run: pip install pynput"
                )

        logger.info("AutoCADScribeAgent started session_id=%d", sid)
        return sid

    def stop(self) -> Optional[int]:
        with self._lock:
            if not self._running:
                return None
            self._running = False
            sid = self._session_id

        if self._click_listener:
            self._click_listener.stop()
            self._click_listener = None

        if self._kb_listener:
            self._kb_listener.stop()
            self._kb_listener = None

        if self._scroll_timer:
            self._scroll_timer.cancel()
            self._scroll_timer = None

        if self._tooltip_poller:
            self._tooltip_poller.stop()
            self._tooltip_poller = None

        if self._trail_monitor:
            self._trail_monitor.stop()
            self._trail_monitor = None

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
                "UPDATE scribe_sessions SET ended_at=?, status='done' WHERE id=?",
                (_utcnow(), sid),
            )
            self._conn.commit()

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
                """SELECT e.event_type, e.timestamp, e.app_name, e.window_title,
                          e.uia_element_name, e.uia_element_type, e.uia_automation_id,
                          e.screenshot_path, e.annotation, e.voice_text, e.voice_confidence,
                          fa.shapes_json
                   FROM scribe_events e
                   LEFT JOIN frame_annotations fa ON fa.event_id = e.id
                   WHERE e.session_id=? ORDER BY e.seq""",
                (sid,),
            ).fetchall()
            target_row = conn.execute(
                "SELECT target_app, background FROM scribe_sessions WHERE id=?", (sid,)
            ).fetchone()
            conn.close()

            events     = [dict(r) for r in rows]
            target_exe = (target_row["target_app"] if target_row else None) or "acad.exe"
            background = (target_row["background"] if target_row else None) or ""
            narration  = _generate_narration_sync(events, lang=lang, target_exe=target_exe,
                                                   background=background)

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

        # Track the most recent invoke name for click-label fallback
        if event.get("event_type") == "uia_invoke":
            name = (event.get("uia_element_name") or "").strip()
            if name:
                with self._lock:
                    self._last_invoke_name = name
                    self._last_invoke_ts   = time.monotonic()

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
                    self._save_screenshot(f"{ScreenshotTrigger.CMD}:{cmd_name}")

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
                annotations = kwargs.pop("_annotations", None)
                cols = ", ".join(kwargs.keys())
                ph   = ", ".join("?" * len(kwargs))
                self._conn.execute(
                    f"INSERT INTO scribe_events ({cols}) VALUES ({ph})",
                    list(kwargs.values()),
                )
                if annotations is not None:
                    event_id   = self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    session_id = kwargs.get("session_id")
                    seq        = kwargs.get("seq", 0)
                    self._conn.execute(
                        """INSERT INTO frame_annotations
                               (session_id, event_id, seq, title, narration, shapes_json)
                           VALUES (?, ?, ?, '', '', ?)
                           ON CONFLICT(session_id, event_id) DO UPDATE SET
                               shapes_json = excluded.shapes_json""",
                        (session_id, event_id, seq, annotations),
                    )
            self._conn.commit()          # one commit for the whole batch
        except Exception as exc:
            logger.debug("batch write failed: %s", exc)

    def _save_screenshot(self, trigger: str = "periodic", click_pos=None, click_label: str = "") -> None:
        """Take screenshot in a background thread so it never blocks event flow.

        click_pos:   optional (cx, cy) normalised image coordinates (0-1).
        click_label: UI element name to fill into the click circle text field.
        When click_pos is provided a click_circle annotation is pre-inserted into
        frame_annotations so the circle is visible immediately in the editor.

        Per-frame voice: if voice capture is running, a PCM snapshot of the last
        15 s of audio is taken at trigger time (before the screenshot is written
        to disk) and transcribed via Whisper.  The result is stored in the
        screenshot event's voice_text / voice_confidence columns so every frame
        carries the speech that was happening when it was captured.
        """
        if not self._screenshot_folder:
            return
        folder = self._screenshot_folder
        with self._lock:
            rid    = self._session_id
            target = self._target_exe
            voice  = self._voice          # capture reference before thread starts

        # ── Snapshot audio immediately at trigger time ────────────────────────
        # Window = time since last screenshot (so each frame only captures the
        # speech that happened in its own interval).  Clamped to 2–60 s so the
        # first frame still gets a reasonable window and very long gaps don't
        # pull in stale audio.
        now_mono = time.monotonic()
        with self._lock:
            last_ts = self._last_screenshot_time
            self._last_screenshot_time = now_mono
        window_secs = float(now_mono - last_ts) if last_ts > 0 else 30.0
        window_secs = max(2.0, min(window_secs, 60.0))

        pcm_snapshot = b""
        if voice is not None:
            try:
                pcm_snapshot = voice.snapshot_pcm(window_secs=window_secs)
                logger.debug(
                    "voice snapshot: trigger=%s window=%.1fs pcm=%d bytes",
                    trigger, window_secs, len(pcm_snapshot),
                )
            except Exception as exc:
                logger.warning("voice snapshot failed: %s", exc)

        def _do():
            # 1. Take screenshot
            ts  = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            rel = _take_screenshot(folder, f"{ts}.jpg", target_exe=target)
            if not rel:
                return

            # 2. Transcribe per-frame voice snapshot (blocking but in a daemon thread)
            voice_text = None
            voice_conf = None
            if pcm_snapshot:
                logger.debug("transcribing per-frame voice (%d bytes)", len(pcm_snapshot))
                text, conf = _transcribe_pcm(pcm_snapshot)
                logger.debug("per-frame voice result: %r (conf=%.2f)", text[:60] if text else "", conf)
                if text:
                    voice_text = text
                    voice_conf = round(conf, 3)
            else:
                logger.debug("per-frame voice: no audio snapshot (voice=%s)", voice is not None)

            # 3. Write screenshot event (with optional voice + click circle)
            event_kwargs = dict(
                session_id=rid,
                event_type="screenshot",
                screenshot_path=rel,
                annotation=trigger,
                voice_text=voice_text,
                voice_confidence=voice_conf,
            )
            if click_pos is not None:
                cx = max(0.0, min(1.0, click_pos[0]))
                cy = max(0.0, min(1.0, click_pos[1]))
                shape = {
                    "id": 1,
                    "type": "click_circle",
                    "points": [cx, cy, 0.035],
                    "color": "#f7768e",
                    "text": click_label,
                    "label_font_size_px": 16,
                }
                event_kwargs["_annotations"] = json.dumps([shape])
            self._write_event(**event_kwargs)

        threading.Thread(target=_do, daemon=True).start()

    def _start_click_listener(self) -> None:
        """Start a global mouse+keyboard listener for click/drag/scroll events."""

        # ── Keyboard listener: track Shift state in real time ─────────────────
        # More reliable than polling GetAsyncKeyState from a pynput hook thread.
        def _on_key_press(key):
            if key in (_kb_lib.Key.shift, _kb_lib.Key.shift_l, _kb_lib.Key.shift_r):
                self._shift_held = True

        def _on_key_release(key):
            if key in (_kb_lib.Key.shift, _kb_lib.Key.shift_l, _kb_lib.Key.shift_r):
                self._shift_held = False

        self._kb_listener = _kb_lib.Listener(
            on_press=_on_key_press, on_release=_on_key_release
        )
        self._kb_listener.daemon = True
        self._kb_listener.start()

        def _resolve_label(_x, _y):
            """Try all label sources in priority order, return (label, source)."""
            label = _get_element_name_at_point(_x, _y)
            source = "uia_point"
            if not label and self._tooltip_poller:
                label = self._tooltip_poller.get(max_age=2.0)
                if label:
                    source = "tooltip"
            if not label and self._trail_monitor:
                label = self._trail_monitor.get_recent_cmd(max_age=1.5)
                if label:
                    source = "trail_file"
            if not label:
                with self._lock:
                    age = time.monotonic() - self._last_invoke_ts
                    invoke_name = self._last_invoke_name if age < 1.0 else ""
                if invoke_name:
                    label  = invoke_name
                    source = "uia_invoke"
            if not label:
                label = _ocr_at_point(_x, _y)
                if label:
                    source = "winrt_ocr"
            return label, source

        def _norm_pos(_x, _y, target):
            """Convert screen coords to normalised (0-1) within the target window."""
            rect = _get_window_rect_for_exe(target)
            if rect and rect["width"] > 0 and rect["height"] > 0:
                return ((_x - rect["left"]) / rect["width"],
                        (_y - rect["top"])  / rect["height"])
            return None

        def _on_click(_x, _y, button, pressed):
            with self._lock:
                if not self._running:
                    return False
                target        = self._target_exe
                on_click      = self._screenshot_on_click
                on_middle     = self._screenshot_on_middle_drag
                on_shift_pan  = self._screenshot_on_shift_pan

            if not _is_target_foreground(target):
                return

            # ── Middle button: rotation (plain) or pan (Shift held) ───────────
            if button == _mouse_lib.Button.middle and (on_middle or on_shift_pan):
                if pressed:
                    with self._lock:
                        self._middle_pressed        = True
                        self._middle_press_pos      = (_x, _y)
                        self._shift_at_middle_press = self._shift_held
                else:
                    with self._lock:
                        was_pressed    = self._middle_pressed
                        sx, sy         = self._middle_press_pos
                        shift_at_press = self._shift_at_middle_press
                        self._middle_pressed = False
                        now = time.monotonic()
                        m_ok = (now - self._last_middle_screenshot)    >= self._middle_cooldown
                        p_ok = (now - self._last_shift_pan_screenshot) >= self._shift_pan_cooldown
                    if was_pressed:
                        dx   = _x - sx
                        dy   = _y - sy
                        dist = (dx ** 2 + dy ** 2) ** 0.5
                        if dist >= self._middle_drag_threshold:
                            click_pos = _norm_pos(_x, _y, target)
                            if shift_at_press and on_shift_pan and p_ok:
                                # Determine pan direction by dominant axis
                                if abs(dx) >= abs(dy):
                                    trigger = (ScreenshotTrigger.SHIFT_PAN_RIGHT
                                               if dx > 0 else ScreenshotTrigger.SHIFT_PAN_LEFT)
                                else:
                                    trigger = (ScreenshotTrigger.SHIFT_PAN_DOWN
                                               if dy > 0 else ScreenshotTrigger.SHIFT_PAN_UP)
                                label = ScreenshotTrigger.LABELS[trigger]
                                with self._lock:
                                    self._last_shift_pan_screenshot = time.monotonic()
                                logger.info("shift+middle pan %s (%d,%d)→(%d,%d) dist=%.0f",
                                            trigger, sx, sy, _x, _y, dist)
                                self._save_screenshot(trigger,
                                                      click_pos=click_pos,
                                                      click_label=label)
                            elif not shift_at_press and on_middle and m_ok:
                                # Determine rotation direction by dominant axis
                                if abs(dx) >= abs(dy):
                                    trigger = (ScreenshotTrigger.ROTATE_RIGHT
                                               if dx > 0 else ScreenshotTrigger.ROTATE_LEFT)
                                else:
                                    trigger = (ScreenshotTrigger.ROTATE_DOWN
                                               if dy > 0 else ScreenshotTrigger.ROTATE_UP)
                                rot_label = ScreenshotTrigger.LABELS[trigger]
                                with self._lock:
                                    self._last_middle_screenshot = time.monotonic()
                                logger.info("middle-drag %s (%d,%d)→(%d,%d) dist=%.0f",
                                            trigger, sx, sy, _x, _y, dist)
                                self._save_screenshot(trigger,
                                                      click_pos=click_pos,
                                                      click_label=rot_label)
                return

            # ── Left / right click ────────────────────────────────────────────
            if not pressed or not on_click:
                return
            if button not in (_mouse_lib.Button.left, _mouse_lib.Button.right):
                return

            now = time.monotonic()
            with self._lock:
                if (now - self._last_click_screenshot) < self._click_cooldown:
                    return
                self._last_click_screenshot = now

            click_pos = _norm_pos(_x, _y, target)
            label, source = _resolve_label(_x, _y)
            logger.info("click at (%d, %d) button=%s  label=%r  source=%s",
                        _x, _y, button.name, label, source)
            trigger = (ScreenshotTrigger.CLICK_LEFT
                       if button == _mouse_lib.Button.left
                       else ScreenshotTrigger.CLICK_RIGHT)
            self._save_screenshot(trigger, click_pos=click_pos, click_label=label)

        def _on_scroll(sx, sy, _dx, dy):
            with self._lock:
                if not self._running:
                    return False
                target    = self._target_exe
                on_scroll = self._screenshot_on_scroll_zoom

            if not on_scroll or not _is_target_foreground(target):
                return

            # Debounce: cancel pending timer, fire screenshot 0.8 s after last tick.
            # Capture position + direction from the *last* scroll tick so the circle
            # lands where the mouse actually stopped scrolling.
            with self._lock:
                if self._scroll_timer:
                    self._scroll_timer.cancel()
                _dy = dy     # direction at time of this tick
                _sx, _sy = sx, sy   # position at time of this tick

            def _fire():
                with self._lock:
                    self._scroll_timer = None
                trigger = (ScreenshotTrigger.SCROLL_ZOOM_OUT
                           if _dy > 0 else ScreenshotTrigger.SCROLL_ZOOM_IN)
                label     = ScreenshotTrigger.LABELS.get(trigger, trigger)
                click_pos = _norm_pos(_sx, _sy, target)
                logger.info("scroll zoom %s at (%d,%d) — capturing", trigger, _sx, _sy)
                self._save_screenshot(trigger, click_pos=click_pos, click_label=label)

            t = threading.Timer(0.8, _fire)
            t.daemon = True
            with self._lock:
                self._scroll_timer = t
            t.start()

        self._click_listener = _mouse_lib.Listener(on_click=_on_click, on_scroll=_on_scroll)
        self._click_listener.daemon = True
        self._click_listener.start()
        logger.info("Mouse listener started (click / scroll / drag)")

    def _periodic_loop(self) -> None:
        while True:
            time.sleep(self._screenshot_interval)
            with self._lock:
                if not self._running:
                    break
            self._save_screenshot(ScreenshotTrigger.PERIODIC)


# Module-level singleton
autocad_agent = AutoCADScribeAgent()