"""
Local video export — generate an MP4 slideshow from session screenshots.

Strategy (in order of availability):
  1. ffmpeg  — proper MP4, best quality, requires ffmpeg.exe on PATH
  2. Pillow  — animated GIF fallback (no external dependency)
  3. ZIP     — last resort: zip up the screenshots for manual use

Public API
----------
    path, mime = build_video(session_id, fps=1.0)
    path, mime = build_narrated_video(session_id, voice="alloy")
    # path: absolute Path to generated file
    # mime: "video/mp4" | "image/gif" | "application/zip"
"""
import os
import shutil
import subprocess
import zipfile
import logging
import sqlite3
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("app.video_export")

try:
    from PIL import Image
    _PIL = True
except ImportError:
    _PIL = False

# Resolve ffmpeg binary: prefer imageio-ffmpeg's bundled copy, fall back to PATH.
def _get_ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    return shutil.which("ffmpeg")

# In-memory job state: session_id → {"status": "generating"|"ready"|"error", "error": str|None}
_job_state: dict[int, dict] = {}


def get_job_state(session_id: int) -> dict:
    return _job_state.get(session_id, {"status": "not_started", "error": None})


def _ffmpeg_available() -> bool:
    return _get_ffmpeg_exe() is not None


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


from app.database import DATA_DIR as _DATA_DIR
_BASE = _DATA_DIR / "videos"


# ── helpers ───────────────────────────────────────────────────────────────────

def _max_frame_size(shots: list[Path]) -> tuple[int, int]:
    """Return (width, height) large enough for every frame — both even numbers."""
    max_w = max_h = 0
    for p in shots:
        try:
            with Image.open(p) as im:
                max_w = max(max_w, im.width)
                max_h = max(max_h, im.height)
        except Exception:
            pass
    # H.264 requires even dimensions
    return (max_w + max_w % 2, max_h + max_h % 2) if max_w and max_h else (2, 2)


def _fit_frame(img: "Image.Image", tw: int, th: int) -> "Image.Image":
    """
    Scale *img* to fit within tw×th while keeping its aspect ratio,
    then centre it on a black canvas of exactly tw×th.
    No stretching — unused areas are filled with black.
    """
    img = img.copy()
    img.thumbnail((tw, th), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (tw, th), (0, 0, 0))
    x = (tw - img.width) // 2
    y = (th - img.height) // 2
    canvas.paste(img, (x, y))
    return canvas


# ── builders ──────────────────────────────────────────────────────────────────

def _build_mp4(shots: list[Path], out: Path, fps: float) -> Path:
    """Use ffmpeg to build an MP4 from the screenshot list."""
    tw, th = _max_frame_size(shots)

    # scale to fit within tw×th (keep AR), pad remaining area with black
    vf = (
        f"scale={tw}:{th}:force_original_aspect_ratio=decrease,"
        f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color=black"
    )

    list_file = out.parent / f"{out.stem}_list.txt"
    duration = 1.0 / fps
    with open(list_file, "w") as f:
        for p in shots:
            f.write(f"file '{p.as_posix()}'\n")
            f.write(f"duration {duration:.3f}\n")
        if shots:
            f.write(f"file '{shots[-1].as_posix()}'\n")

    cmd = [
        _get_ffmpeg_exe(), "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-vf", vf,
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
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
    # Cap at 960 px wide to keep GIF size manageable
    max_w = 960
    raw_frames = [Image.open(p).convert("RGB") for p in shots]
    if not raw_frames:
        raise ValueError("No frames to encode")

    tw = min(max_w, max(f.width  for f in raw_frames))
    th = max(int(f.height * tw / f.width) if f.width else f.height
             for f in raw_frames)
    # Use the tallest frame's height after scaling — then fit-pad everything
    frames = [_fit_frame(f, tw, th) for f in raw_frames]

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

    # Pre-scan to find the canvas size (max width × max height across all frames)
    src_paths = [
        Path(r["screenshot_dir"]) / r["screenshot_path"]
        for r in rows
        if (Path(r["screenshot_dir"]) / r["screenshot_path"]).exists()
    ]
    canvas_w, canvas_h = _max_frame_size(src_paths)

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
                        font_px = shape.get("label_font_size_px") or max(14, int(rad * 0.45))
                        font = _load_cjk_font(font_px)
                        lx = cx
                        ly = cy_px - rad - font_px - 8
                        draw.text((lx + 1, ly + 1), label, font=font, fill="#000000", anchor="mm")
                        draw.text((lx,     ly),     label, font=font, fill=color,     anchor="mm")

            # Fit into the uniform canvas — no stretching
            img = _fit_frame(img, canvas_w, canvas_h)
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
    Prefers narrated > annotated > plain versions."""
    out_dir = _BASE / str(session_id)
    candidates = [
        (f"session_{session_id}_narrated.mp4",    "video/mp4"),
        (f"session_{session_id}_annotated.mp4",   "video/mp4"),
        (f"session_{session_id}_annotated.gif",   "image/gif"),
        (f"session_{session_id}.mp4",              "video/mp4"),
        (f"session_{session_id}.gif",              "image/gif"),
        (f"session_{session_id}_screenshots.zip",  "application/zip"),
    ]
    for filename, mime in candidates:
        p = out_dir / filename
        if p.exists():
            return p, mime
    return None


# ── Narrated video (TTS per frame) ────────────────────────────────────────────

# Azure OpenAI TTS endpoint (same base as Whisper)
_AZURE_TTS_URL = (
    "https://oai-seaidev-concept-advisor.cognitiveservices.azure.com"
    "/openai/deployments/{deployment}/audio/speech"
    "?api-version=2025-03-01-preview"
)
_DEFAULT_TTS_DEPLOYMENT = "gpt-4o-mini-tts"
_TTS_PAUSE_AFTER = 0.5        # seconds of silence appended after each narration
_TTS_FALLBACK_DURATION = 4.0  # seconds per frame when TTS unavailable / no narration

# Job state for narrated video (separate from annotated job state)
_narrated_job: dict[int, dict] = {}


def get_narrated_job_state(session_id: int) -> dict:
    state = _narrated_job.get(session_id)
    if state:
        return state
    # In-memory state is lost on restart — check disk as fallback
    video_path = _BASE / str(session_id) / f"session_{session_id}_narrated.mp4"
    if video_path.exists():
        return {"status": "ready", "error": None}
    return {"status": "not_started", "error": None}


def _tts_to_file(text: str, voice: str, out_path: Path) -> bool:
    """Call Azure OpenAI TTS, save MP3 to out_path.  Returns True on success."""
    if not text.strip():
        return False
    api_key = os.environ.get("AZURE_TTS_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("TTS skipped: set AZURE_TTS_API_KEY or AZURE_OPENAI_API_KEY in .env")
        return False
    deployment = os.environ.get("AZURE_TTS_DEPLOYMENT", _DEFAULT_TTS_DEPLOYMENT)
    url = _AZURE_TTS_URL.format(deployment=deployment)
    try:
        import httpx
        resp = httpx.post(
            url,
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json={"model": deployment, "input": text, "voice": voice},
            timeout=60,
        )
        resp.raise_for_status()
        out_path.write_bytes(resp.content)
        logger.debug("TTS ok: %d bytes → %s", len(resp.content), out_path.name)
        return True
    except Exception as exc:
        logger.warning("TTS failed: %s", exc)
        return False


def _audio_duration(path: Path) -> float:
    """Use ffprobe to get audio duration in seconds."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             str(path)],
            capture_output=True, timeout=15,
        )
        return max(0.1, float(result.stdout.strip()))
    except Exception:
        return _TTS_FALLBACK_DURATION


def _make_frame_clip(
    img_path: Path,
    audio_path: Optional[Path],
    duration: float,
    out_path: Path,
    canvas_w: int,
    canvas_h: int,
) -> None:
    """Encode one annotated image + optional audio into a short MP4 clip."""
    vf = (
        f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,"
        f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:color=black"
    )
    cmd = [_get_ffmpeg_exe(), "-y", "-loop", "1", "-framerate", "24", "-i", str(img_path)]
    if audio_path:
        cmd += ["-i", str(audio_path)]
    cmd += ["-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-t", str(duration)]
    if audio_path:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    cmd.append(str(out_path))

    result = subprocess.run(cmd, capture_output=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg clip failed: {result.stderr.decode()[-400:]}")


def _load_cjk_font(size: int):
    """Return an ImageFont that supports CJK characters at the given pixel size.

    Tries common Windows / macOS / Linux font paths in order.
    Falls back to PIL's default font if nothing is found (Chinese will be garbled).
    """
    from PIL import ImageFont

    candidates = [
        # Windows
        r"C:\Windows\Fonts\msyh.ttc",        # 微软雅黑
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",       # 黑体
        r"C:\Windows\Fonts\simsun.ttc",       # 宋体
        r"C:\Windows\Fonts\STZHONGS.TTF",
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/Library/Fonts/Arial Unicode MS.ttf",
        # Linux (common distros)
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    # Last resort: PIL built-in (no CJK support)
    return ImageFont.load_default()


def _render_annotated_frame(
    src: Path,
    shapes: list,
    canvas_w: int,
    canvas_h: int,
    out_path: Path,
) -> None:
    """Render annotations onto the screenshot and save to out_path (JPEG)."""
    from PIL import Image, ImageDraw, ImageFilter
    img = Image.open(src).convert("RGB")
    w, h = img.size

    for shape in shapes:
        stype = shape.get("type")
        if stype == "blur_region":
            pts = shape.get("points", [])
            if len(pts) >= 4:
                nx, ny, nw, nh = pts[0], pts[1], pts[2], pts[3]
                x1, y1 = int(nx * w), int(ny * h)
                x2, y2 = int((nx + nw) * w), int((ny + nh) * h)
                if x2 > x1 and y2 > y1:
                    radius = max(2, int(shape.get("intensity", 10) * max(w, h) / 1000))
                    region = img.crop((x1, y1, x2, y2))
                    img.paste(region.filter(ImageFilter.GaussianBlur(radius=radius)), (x1, y1))
        elif stype == "click_circle":
            pts = shape.get("points", [])
            if len(pts) >= 3:
                cx = int(pts[0] * w)
                cy_px = int(pts[1] * h)
                rad = int(pts[2] * min(w, h))
                color = shape.get("color", "#ff4d4f")
                draw = ImageDraw.Draw(img)
                lw = max(2, int(rad * 0.08))
                draw.ellipse([(cx - rad, cy_px - rad), (cx + rad, cy_px + rad)],
                             outline=color, width=lw)
                label = (shape.get("text") or "").strip()
                if label:
                    font_px = shape.get("label_font_size_px") or max(14, int(rad * 0.45))
                    font = _load_cjk_font(font_px)
                    lx, ly = cx, cy_px - rad - font_px - 8
                    draw.text((lx + 1, ly + 1), label, font=font, fill="#000000", anchor="mm")
                    draw.text((lx, ly),          label, font=font, fill=color,    anchor="mm")

    img = _fit_frame(img, canvas_w, canvas_h)
    img.save(str(out_path), format="JPEG", quality=90)


def build_narrated_video(session_id: int, voice: str = "alloy") -> tuple[Path, str]:
    """
    Generate a narrated MP4: each frame is displayed for the TTS duration of its
    narration text.  Frames without narration use a 4-second fallback.

    Pipeline (mirrors AutoScribe video_export_impl.py):
      For each frame:
        1. Render annotations (blur regions + click circles) onto screenshot  (Pillow)
        2. TTS: narration text → Azure OpenAI gpt-4o-mini-tts → MP3
        3. ffprobe: MP3 duration + 0.5 s pause = clip duration
        4. ffmpeg: looped image + MP3 audio → per-frame .mp4 clip
      Concatenate all clips with: ffmpeg -f concat -c copy → final MP4
    """
    _narrated_job[session_id] = {"status": "generating", "error": None}
    try:
        result = _build_narrated_inner(session_id, voice)
        _narrated_job[session_id] = {"status": "ready", "error": None}
        return result
    except Exception as exc:
        _narrated_job[session_id] = {"status": "error", "error": str(exc)}
        raise


def _build_narrated_inner(session_id: int, voice: str) -> tuple[Path, str]:
    if not _ffmpeg_available():
        raise ValueError("ffmpeg is required for narrated video — install ffmpeg and add to PATH")
    if not _PIL:
        raise ValueError("Pillow is required for narrated video — pip install Pillow")

    import json

    from app.database import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT e.id          AS event_id,
                  e.seq,
                  e.screenshot_path,
                  s.screenshot_dir,
                  COALESCE(fa.shapes_json,  '[]') AS shapes_json,
                  COALESCE(fa.narration,    '')   AS narration
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

    # Canvas size = max(width) × max(height) across all source frames
    src_paths = [
        Path(r["screenshot_dir"]) / r["screenshot_path"]
        for r in rows
        if (Path(r["screenshot_dir"]) / r["screenshot_path"]).exists()
    ]
    canvas_w, canvas_h = _max_frame_size(src_paths)

    tmp = Path(tempfile.mkdtemp(prefix="apptrack_narrated_"))
    clips: list[Path] = []

    try:
        for i, row in enumerate(rows):
            src = Path(row["screenshot_dir"]) / row["screenshot_path"]
            if not src.exists():
                logger.warning("Frame %d missing: %s", i, src)
                continue

            narration = (row["narration"] or "").strip()
            shapes    = json.loads(row["shapes_json"] or "[]")

            # 1. Render annotated frame
            frame_path = tmp / f"{i:06d}.jpg"
            _render_annotated_frame(src, shapes, canvas_w, canvas_h, frame_path)

            # 2. TTS → MP3
            audio_path: Optional[Path] = None
            duration = _TTS_FALLBACK_DURATION
            if narration:
                mp3_path = tmp / f"{i:06d}.mp3"
                if _tts_to_file(narration, voice, mp3_path):
                    audio_path = mp3_path
                    duration   = _audio_duration(mp3_path) + _TTS_PAUSE_AFTER

            logger.info(
                "Frame %d/%d  dur=%.1fs  tts=%s  narration=%r",
                i + 1, len(rows), duration,
                "ok" if audio_path else "fallback",
                narration[:50] if narration else "(none)",
            )

            # 3. Encode per-frame clip
            clip_path = tmp / f"{i:06d}_clip.mp4"
            _make_frame_clip(frame_path, audio_path, duration, clip_path, canvas_w, canvas_h)
            clips.append(clip_path)

        if not clips:
            raise ValueError("No valid clips generated")

        # 4. Concatenate all clips
        concat_list = tmp / "concat.txt"
        with open(concat_list, "w") as f:
            for clip in clips:
                f.write(f"file '{clip.as_posix()}'\n")

        out_dir = _output_dir(session_id)
        out = out_dir / f"session_{session_id}_narrated.mp4"
        result = subprocess.run(
            [_get_ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_list), "-c", "copy", str(out)],
            capture_output=True, timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {result.stderr.decode()[-400:]}")

        logger.info("Narrated video ready: %s", out)
        return out, "video/mp4"

    finally:
        shutil.rmtree(tmp, ignore_errors=True)