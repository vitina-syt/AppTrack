"""
Local video export — generate an MP4 slideshow from session screenshots.

Strategy (in order of availability):
  1. ffmpeg  — proper MP4, best quality, requires ffmpeg.exe on PATH
  2. Pillow  — animated GIF fallback (no external dependency)
  3. ZIP     — last resort: zip up the screenshots for manual use

Public API
----------
    path, mime = build_video(session_id, fps=1.0)
    # path: absolute Path to generated file
    # mime: "video/mp4" | "image/gif" | "application/zip"
"""
import shutil
import subprocess
import zipfile
import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger("app.video_export")

try:
    from PIL import Image
    _PIL = True
except ImportError:
    _PIL = False

# In-memory job state: session_id → {"status": "generating"|"ready"|"error", "error": str|None}
_job_state: dict[int, dict] = {}


def get_job_state(session_id: int) -> dict:
    return _job_state.get(session_id, {"status": "not_started", "error": None})


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _screenshot_paths(session_id: int) -> list[Path]:
    """Return sorted absolute paths of all screenshots for this session."""
    from app.database import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT e.screenshot_path, s.screenshot_dir
           FROM scribe_events e
           JOIN scribe_sessions s ON s.id = e.session_id
           WHERE e.session_id = ? AND e.event_type = 'screenshot'
             AND e.screenshot_path IS NOT NULL
           ORDER BY e.seq""",
        (session_id,),
    ).fetchall()
    conn.close()

    paths = []
    for r in rows:
        p = Path(r["screenshot_dir"]) / r["screenshot_path"]
        if p.exists():
            paths.append(p)
    return paths


def _output_dir(session_id: int) -> Path:
    from app.video_export import _BASE
    d = _BASE / str(session_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


_BASE = Path(__file__).parent.parent / "data" / "videos"


# ── builders ──────────────────────────────────────────────────────────────────

def _build_mp4(shots: list[Path], out: Path, fps: float) -> Path:
    """Use ffmpeg to build an MP4 from the screenshot list."""
    # Write a text file listing all frames (concat demuxer)
    list_file = out.parent / f"{out.stem}_list.txt"
    duration = 1.0 / fps
    with open(list_file, "w") as f:
        for p in shots:
            # ffmpeg concat demuxer requires forward slashes even on Windows
            f.write(f"file '{p.as_posix()}'\n")
            f.write(f"duration {duration:.3f}\n")
        # Repeat last frame so it shows properly
        if shots:
            f.write(f"file '{shots[-1].as_posix()}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",   # ensure even dimensions
        "-c:v", "libx264", "-crf", "28", "-preset", "fast",
        "-pix_fmt", "yuv420p",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    list_file.unlink(missing_ok=True)

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.decode()[-500:]}")
    return out


def _build_gif(shots: list[Path], out: Path, fps: float) -> Path:
    """Use Pillow to build an animated GIF (fallback)."""
    frames = []
    for p in shots:
        img = Image.open(p).convert("RGB")
        # Scale down — GIF at full res is huge
        max_w = 960
        if img.width > max_w:
            ratio = max_w / img.width
            img = img.resize((max_w, int(img.height * ratio)), Image.Resampling.LANCZOS)
        frames.append(img)

    if not frames:
        raise ValueError("No frames to encode")

    duration_ms = int(1000 / fps)
    frames[0].save(
        out,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=0,
        optimize=False,
    )
    return out


def _build_zip(shots: list[Path], out: Path) -> Path:
    """Last resort: zip all screenshots."""
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, p in enumerate(shots):
            zf.write(p, f"{i:04d}_{p.name}")
    return out


# ── public ────────────────────────────────────────────────────────────────────

def build_video(session_id: int, fps: float = 1.0) -> tuple[Path, str]:
    """Wrapper that updates _job_state before/after building."""
    _job_state[session_id] = {"status": "generating", "error": None}
    try:
        result = _build_video_inner(session_id, fps)
        _job_state[session_id] = {"status": "ready", "error": None}
        return result
    except Exception as exc:
        _job_state[session_id] = {"status": "error", "error": str(exc)}
        raise


def _build_video_inner(session_id: int, fps: float = 1.0) -> tuple[Path, str]:
    """
    Generate a video file from the screenshots of *session_id*.

    Parameters
    ----------
    session_id : int
    fps : float  — frames per second (default 1 = one screenshot per second)

    Returns
    -------
    (path, mime_type)
    """
    shots = _screenshot_paths(session_id)
    if not shots:
        raise ValueError("No screenshots found for this session")

    out_dir = _output_dir(session_id)

    if _ffmpeg_available():
        out = out_dir / f"session_{session_id}.mp4"
        logger.info("Building MP4 via ffmpeg (%d frames, %.1f fps)", len(shots), fps)
        return _build_mp4(shots, out, fps), "video/mp4"

    if _PIL:
        out = out_dir / f"session_{session_id}.gif"
        logger.info("Building GIF via Pillow (%d frames, %.1f fps)", len(shots), fps)
        return _build_gif(shots, out, fps), "image/gif"

    out = out_dir / f"session_{session_id}_screenshots.zip"
    logger.info("Falling back to ZIP (%d screenshots)", len(shots))
    return _build_zip(shots, out), "application/zip"


def build_annotated_video(session_id: int, fps: float = 1.0) -> tuple[Path, str]:
    """
    Generate a video with annotation shapes burned into each frame.
    Reads shapes from the frame_annotations table.
    Requires Pillow.  Falls back to ZIP if Pillow is unavailable.
    """
    if not _PIL:
        raise ValueError("Pillow is required for annotated video — pip install Pillow")

    import json
    import sqlite3
    import tempfile
    import shutil as _shutil
    from PIL import Image, ImageDraw, ImageFilter

    from app.database import DB_PATH

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT e.id          AS event_id,
                  e.seq,
                  e.screenshot_path,
                  s.screenshot_dir,
                  COALESCE(fa.shapes_json, '[]') AS shapes_json
           FROM   scribe_events  e
           JOIN   scribe_sessions s  ON s.id = e.session_id
           LEFT JOIN frame_annotations fa
                  ON fa.event_id = e.id AND fa.session_id = ?
           WHERE  e.session_id = ? AND e.event_type = 'screenshot'
           ORDER BY e.seq""",
        (session_id, session_id),
    ).fetchall()
    conn.close()

    if not rows:
        raise ValueError("No screenshot frames found for this session")

    temp_dir = Path(tempfile.mkdtemp(prefix="apptrack_annotated_"))
    annotated: list[Path] = []

    try:
        for row in rows:
            src = Path(row["screenshot_dir"]) / row["screenshot_path"]
            if not src.exists():
                continue

            img    = Image.open(src).convert("RGB")
            w, h   = img.size
            shapes = json.loads(row["shapes_json"] or "[]")

            for shape in shapes:
                stype = shape.get("type")

                # ── blur_region (AutoScribe format) ──────────────────────
                if stype == "blur_region":
                    pts = shape.get("points", [])
                    if len(pts) < 4:
                        continue
                    nx, ny, nw, nh = pts[0], pts[1], pts[2], pts[3]
                    x1 = int(nx * w)
                    y1 = int(ny * h)
                    x2 = int((nx + nw) * w)
                    y2 = int((ny + nh) * h)
                    if x2 > x1 and y2 > y1:
                        intensity = shape.get("intensity", 10)
                        radius    = max(2, int(intensity * max(w, h) / 1000))
                        region    = img.crop((x1, y1, x2, y2))
                        blurred   = region.filter(ImageFilter.GaussianBlur(radius=radius))
                        img.paste(blurred, (x1, y1))

                # ── click_circle (AutoScribe format) ─────────────────────
                elif stype == "click_circle":
                    pts = shape.get("points", [])
                    if len(pts) < 3:
                        continue
                    cx_n, cy_n, r_n = pts[0], pts[1], pts[2]
                    cx    = int(cx_n * w)
                    cy_px = int(cy_n * h)
                    rad   = int(r_n * min(w, h))
                    color = shape.get("color", "#ff4d4f")
                    draw  = ImageDraw.Draw(img)
                    lw    = max(2, int(rad * 0.08))
                    draw.ellipse(
                        [(cx - rad, cy_px - rad), (cx + rad, cy_px + rad)],
                        outline=color, width=lw,
                    )
                    label = (shape.get("text") or "").strip()
                    if label:
                        # Auto font size matching canvas rendering
                        font_px = shape.get("label_font_size_px") or 0
                        if not font_px:
                            font_px = max(12, int(rad * 0.45))
                        lx = cx
                        ly = cy_px - rad - font_px - 8
                        draw.text((lx + 1, ly + 1), label, fill="#000000", anchor="mm")
                        draw.text((lx,     ly),     label, fill=color,     anchor="mm")

            out_path = temp_dir / f"{row['seq']:06d}.jpg"
            img.save(str(out_path), format="JPEG", quality=90)
            annotated.append(out_path)

        if not annotated:
            raise ValueError("No valid annotated frames")

        _job_state[session_id] = {"status": "generating", "error": None}
        out_dir = _output_dir(session_id)

        try:
            if _ffmpeg_available():
                out = out_dir / f"session_{session_id}_annotated.mp4"
                result = _build_mp4(annotated, out, fps)
                _job_state[session_id] = {"status": "ready", "error": None}
                return result, "video/mp4"
            else:
                out = out_dir / f"session_{session_id}_annotated.gif"
                result = _build_gif(annotated, out, fps)
                _job_state[session_id] = {"status": "ready", "error": None}
                return result, "image/gif"
        except Exception as exc:
            _job_state[session_id] = {"status": "error", "error": str(exc)}
            raise

    finally:
        _shutil.rmtree(temp_dir, ignore_errors=True)


def get_existing_video(session_id: int) -> Optional[tuple[Path, str]]:
    """Return (path, mime) if a video was already generated, else None.
    Prefers annotated version if present."""
    out_dir = _BASE / str(session_id)
    candidates = [
        (f"session_{session_id}_annotated.mp4",  "video/mp4"),
        (f"session_{session_id}_annotated.gif",  "image/gif"),
        (f"session_{session_id}.mp4",             "video/mp4"),
        (f"session_{session_id}.gif",             "image/gif"),
        (f"session_{session_id}_screenshots.zip", "application/zip"),
    ]
    for filename, mime in candidates:
        p = out_dir / filename
        if p.exists():
            return p, mime
    return None