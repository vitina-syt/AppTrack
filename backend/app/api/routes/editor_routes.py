"""
Frame Editor API  —  /api/autocad/sessions/{id}/frames/*

GET    /api/autocad/sessions/{id}/frames              — list all screenshot frames + annotations
PATCH  /api/autocad/sessions/{id}/frames/{event_id}   — save title / narration / shapes for one frame
POST   /api/autocad/sessions/{id}/frames/distribute   — split session narration into per-frame chunks
POST   /api/autocad/sessions/{id}/video/annotated     — generate video with annotations burned in

Annotation shape JSON format (stored in shapes_json column):
  {"id": int, "type": "circle", "cx": 0-1, "cy": 0-1, "rx": 0-1, "ry": 0-1,
   "label": str, "color": str}
  {"id": int, "type": "blur",   "x": 0-1, "y": 0-1, "w": 0-1, "h": 0-1}
  {"id": int, "type": "text",   "x": 0-1, "y": 0-1, "text": str,
   "color": str, "size": int}
All coordinates are relative to image dimensions (0–1).
"""
import json
import re
import threading
import logging
from typing import Optional
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.database import get_conn

logger = logging.getLogger("app.editor_routes")

router = APIRouter(prefix="/api/autocad", tags=["autocad-editor"])


# ── models ────────────────────────────────────────────────────────────────────

class FrameUpdate(BaseModel):
    title:       Optional[str] = None
    narration:   Optional[str] = None
    shapes_json: Optional[str] = None   # JSON string


# ── list frames ───────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/frames")
def list_frames(session_id: int):
    """Return all screenshot frames for a session with their annotations."""
    conn = get_conn()
    if not conn.execute(
        "SELECT id FROM scribe_sessions WHERE id=?",
        (session_id,),
    ).fetchone():
        raise HTTPException(status_code=404, detail="Session not found")

    rows = conn.execute(
        """SELECT e.id          AS event_id,
                  e.seq,
                  e.screenshot_path,
                  e.voice_text,
                  e.voice_confidence,
                  s.screenshot_dir,
                  COALESCE(fa.title,       '')   AS title,
                  COALESCE(fa.narration,   '')   AS narration,
                  COALESCE(fa.shapes_json, '[]') AS shapes_json
           FROM   scribe_events  e
           JOIN   scribe_sessions s  ON s.id = e.session_id
           LEFT JOIN frame_annotations fa
                  ON fa.event_id = e.id AND fa.session_id = ?
           WHERE  e.session_id = ? AND e.event_type = 'screenshot'
           ORDER BY e.seq""",
        (session_id, session_id),
    ).fetchall()
    return [dict(r) for r in rows]


# ── update one frame ──────────────────────────────────────────────────────────

@router.patch("/sessions/{session_id}/frames/{event_id}")
def update_frame(session_id: int, event_id: int, body: FrameUpdate):
    """Upsert title / narration / shapes for one frame."""
    conn = get_conn()
    # Validate event belongs to session
    row = conn.execute(
        "SELECT seq FROM scribe_events WHERE id=? AND session_id=?",
        (event_id, session_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Frame not found")

    conn.execute(
        """INSERT INTO frame_annotations
               (session_id, event_id, seq, title, narration, shapes_json)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(session_id, event_id) DO UPDATE SET
               title       = CASE WHEN excluded.title       != '' THEN excluded.title       ELSE title       END,
               narration   = CASE WHEN excluded.narration   != '' THEN excluded.narration   ELSE narration   END,
               shapes_json = excluded.shapes_json""",
        (
            session_id,
            event_id,
            row["seq"],
            body.title     or "",
            body.narration or "",
            body.shapes_json if body.shapes_json is not None else "[]",
        ),
    )
    conn.commit()
    return {"ok": True}


# ── distribute narration ──────────────────────────────────────────────────────

# Human-readable trigger labels (mirrors ScreenshotTrigger.LABELS in autocad_agent.py)
_TRIGGER_LABELS: dict[str, str] = {
    "periodic":                    "定时截图",
    "cmd":                         "命令截图",
    "click:left":                  "左键点击",
    "click:right":                 "右键点击",
    "middle_drag:rotate_left":     "中键左旋转",
    "middle_drag:rotate_right":    "中键右旋转",
    "middle_drag:rotate_up":       "中键上旋转",
    "middle_drag:rotate_down":     "中键下旋转",
    "scroll:zoom_in":              "滚轮放大",
    "scroll:zoom_out":             "滚轮缩小",
    "shift_middle:pan_left":       "Shift+中键左平移",
    "shift_middle:pan_right":      "Shift+中键右平移",
    "shift_middle:pan_up":         "Shift+中键上平移",
    "shift_middle:pan_down":       "Shift+中键下平移",
}


def _extract_circle_info(shapes_json_str: str) -> list[str]:
    """Extract labels from all circle/click_circle shapes in shapes_json.

    Handles both formats:
      - click_circle (auto-inserted from recording): { type, points, text }
      - circle (manually drawn in editor):           { type, label }
    Returns a list of non-empty label strings.
    """
    try:
        shapes = json.loads(shapes_json_str or "[]")
    except Exception:
        return []
    labels = []
    for s in shapes:
        t = s.get("type", "")
        if t == "click_circle":
            label = (s.get("text") or "").strip()
        elif t == "circle":
            label = (s.get("label") or "").strip()
        else:
            continue
        if label:
            labels.append(label)
    return labels


def _ai_frame_narrations(
    frame_data: list[dict],
    background: str,
) -> list[str]:
    """
    Single batched GPT call: for every frame combine
      • trigger type (what caused the screenshot)
      • voice_text  (speech recorded during this frame's interval)
      • circle labels (UI elements highlighted in the image)
    and produce one professional narration per frame.

    frame_data items:
      { event_id, seq, trigger, trigger_label, voice_text, circle_labels: list[str] }
    Returns a list of narration strings (same length as frame_data).
    """
    n = len(frame_data)

    # ── Build prompt ──────────────────────────────────────────────────────────
    parts: list[str] = []
    if background:
        parts.append(f"课程背景：{background}\n")
    parts.append("以下是操作录屏的逐帧信息，请为每帧生成专业的教学解说词。\n")

    for i, fd in enumerate(frame_data):
        parts.append(f"[步骤{i + 1}]")
        parts.append(f"  操作类型：{fd['trigger_label']}")
        if fd["voice_text"]:
            parts.append(f"  讲师语音：{fd['voice_text']}")
        if fd["circle_labels"]:
            parts.append(f"  画面标注：{', '.join(fd['circle_labels'])}")
        if not fd["voice_text"] and not fd["circle_labels"]:
            parts.append("  （无语音，无画面标注）")

    parts.append(
        "\n输出要求：\n"
        "- 严格按照以下格式，每步一行，不要有多余文字：\n"
        "  [步骤1] 解说文字\n"
        "  [步骤2] 解说文字\n"
        "- 以讲师语音为核心内容，忠实保留操作意图和关键信息\n"
        "- 结合操作类型和画面标注补充说明点击位置、操作目标\n"
        "- 语言专业流畅，每步不超过 80 字\n"
        "- 无语音的步骤根据操作类型和上下文合理推断\n"
        "- 输出语言与讲师语音语言保持一致；若无语音则与课程背景语言一致"
    )
    prompt = "\n".join(parts)

    # ── Call GPT ──────────────────────────────────────────────────────────────
    gpt_output = None
    try:
        from app.gpt_assistant import GPTAssistant
        gpt = GPTAssistant()
        gpt.system_prompt = (
            "你是专业的 CAD 软件操作教学视频解说员。"
            "根据每帧的操作类型、讲师语音和画面标注，生成简洁、专业、自然流畅的解说词。"
            "解说词要与实际操作紧密对应，让观众清楚理解每一步在做什么。"
        )
        gpt_output = gpt.chat(prompt)
        logger.info("GPT narration generated for %d frames", n)
    except Exception as exc:
        logger.warning("GPT per-frame narration failed: %s", exc)

    if gpt_output:
        narrations = [""] * n
        for m in re.finditer(r"\[步骤\s*(\d+)\]\s*(.+?)(?=\[步骤|\Z)", gpt_output, re.DOTALL):
            idx = int(m.group(1)) - 1
            if 0 <= idx < n:
                narrations[idx] = m.group(2).strip()
        # Fill any GPT-skipped frames with voice_text fallback
        for i, fd in enumerate(frame_data):
            if not narrations[i] and fd["voice_text"]:
                narrations[i] = fd["voice_text"]
        return narrations

    # ── Fallback: use voice_text directly, blank otherwise ────────────────────
    return [fd["voice_text"] for fd in frame_data]


@router.post("/sessions/{session_id}/frames/distribute")
def distribute_narration(session_id: int):
    """
    For each frame, combine trigger type + recorded voice + circle annotations,
    send to GPT in a single batched call to generate professional per-frame narration.
    Falls back to raw voice_text if GPT is unavailable.
    """
    conn = get_conn()
    sess = conn.execute(
        "SELECT background FROM scribe_sessions WHERE id=?",
        (session_id,),
    ).fetchone()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    rows = conn.execute(
        """SELECT e.id          AS event_id,
                  e.seq,
                  e.annotation                         AS trigger,
                  COALESCE(e.voice_text,      '')      AS voice_text,
                  COALESCE(fa.shapes_json,    '[]')    AS shapes_json
           FROM   scribe_events e
           LEFT JOIN frame_annotations fa
                  ON fa.event_id = e.id AND fa.session_id = ?
           WHERE  e.session_id = ? AND e.event_type = 'screenshot'
           ORDER BY e.seq""",
        (session_id, session_id),
    ).fetchall()

    if not rows:
        raise HTTPException(status_code=422, detail="No screenshot frames found")

    frame_data = [
        {
            "event_id":      r["event_id"],
            "seq":           r["seq"],
            "trigger":       r["trigger"] or "",
            "trigger_label": _TRIGGER_LABELS.get(r["trigger"] or "", r["trigger"] or "截图"),
            "voice_text":    (r["voice_text"] or "").strip(),
            "circle_labels": _extract_circle_info(r["shapes_json"]),
        }
        for r in rows
    ]

    background = (sess["background"] or "").strip()

    has_any = any(fd["voice_text"] or fd["circle_labels"] for fd in frame_data)
    if not has_any and not background:
        raise HTTPException(
            status_code=422,
            detail="无语音、无标注、无背景说明 — 请先录制语音或填写背景说明",
        )

    narrations = _ai_frame_narrations(frame_data, background)

    for i, (fd, narration) in enumerate(zip(frame_data, narrations)):
        conn.execute(
            """INSERT INTO frame_annotations
                   (session_id, event_id, seq, title, narration, shapes_json)
               VALUES (?, ?, ?, ?, ?, '[]')
               ON CONFLICT(session_id, event_id) DO UPDATE SET
                   title     = excluded.title,
                   narration = excluded.narration""",
            (session_id, fd["event_id"], fd["seq"], f"步骤 {i + 1}", narration),
        )

    conn.commit()
    return {
        "ok":               True,
        "frames_updated":   len(frame_data),
        "voice_frames":     sum(1 for fd in frame_data if fd["voice_text"]),
        "annotated_frames": sum(1 for fd in frame_data if fd["circle_labels"]),
        "ai_generated":     True,
    }


# ── annotated video ───────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/video/annotated")
def generate_annotated_video(
    session_id: int,
    fps: float = Query(default=1.0, ge=0.1, le=10.0),
):
    """
    Generate a video with annotation shapes (circles, blur, text) burned into
    every frame.  Runs in background; poll /video/status to check progress.
    """
    conn = get_conn()
    if not conn.execute(
        "SELECT id FROM scribe_sessions WHERE id=? AND target_app='acad.exe'",
        (session_id,),
    ).fetchone():
        raise HTTPException(status_code=404, detail="AutoCAD session not found")

    def _do():
        try:
            from app.video_export import build_annotated_video
            build_annotated_video(session_id, fps=fps)
        except Exception as exc:
            logger.error("Annotated video generation failed for session %d: %s", session_id, exc)

    threading.Thread(target=_do, daemon=True).start()
    return {"ok": True, "session_id": session_id, "status": "generating"}


# ── narrated video (TTS per frame) ───────────────────────────────────────────

_ALLOWED_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}


_ALLOWED_LANGS = {"zh", "en", "de"}


@router.post("/sessions/{session_id}/video/narrated")
def generate_narrated_video(
    session_id: int,
    voice: str = Query(default="alloy"),
    lang:  str = Query(default="zh", description="Subtitle / TTS language: zh | en | de"),
):
    """
    Generate a narrated MP4: each frame is shown for the TTS duration of its
    narration text.  Runs in background; poll /video/narrated/status.
    Requires AZURE_TTS_API_KEY (or AZURE_OPENAI_API_KEY) in .env.
    """
    if voice not in _ALLOWED_VOICES:
        raise HTTPException(status_code=422, detail=f"voice must be one of {sorted(_ALLOWED_VOICES)}")
    if lang not in _ALLOWED_LANGS:
        raise HTTPException(status_code=422, detail=f"lang must be one of {sorted(_ALLOWED_LANGS)}")

    conn = get_conn()
    if not conn.execute("SELECT id FROM scribe_sessions WHERE id=?", (session_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Session not found")

    def _do():
        try:
            from app.video_export import build_narrated_video
            build_narrated_video(session_id, voice=voice, lang=lang)
        except Exception as exc:
            logger.error("Narrated video failed for session %d: %s", session_id, exc)

    threading.Thread(target=_do, daemon=True).start()
    return {"ok": True, "session_id": session_id, "status": "generating", "voice": voice, "lang": lang}


@router.get("/sessions/{session_id}/video/narrated/status")
def narrated_video_status(session_id: int):
    from app.video_export import get_narrated_job_state
    return get_narrated_job_state(session_id)


@router.get("/sessions/{session_id}/video/narrated/download")
def download_narrated_video(session_id: int):
    from app.video_export import _BASE
    path = _BASE / str(session_id) / f"session_{session_id}_narrated.mp4"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Narrated video not found")
    return FileResponse(
        str(path),
        media_type="video/mp4",
        filename=f"narrated_{session_id}.mp4",
    )


# ── delete frame ──────────────────────────────────────────────────────────────

@router.delete("/sessions/{session_id}/frames/{event_id}")
def delete_frame(session_id: int, event_id: int):
    """Delete a screenshot frame and its annotation; remove the image file from disk."""
    conn = get_conn()
    row = conn.execute(
        """SELECT e.screenshot_path, s.screenshot_dir
           FROM scribe_events e
           JOIN scribe_sessions s ON s.id = e.session_id
           WHERE e.id=? AND e.session_id=? AND e.event_type='screenshot'""",
        (event_id, session_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Frame not found")

    # Remove image file from disk
    if row["screenshot_path"] and row["screenshot_dir"]:
        p = Path(row["screenshot_dir"]) / row["screenshot_path"]
        p.unlink(missing_ok=True)

    # Delete annotation then event (FK may not cascade depending on SQLite config)
    conn.execute("DELETE FROM frame_annotations WHERE event_id=? AND session_id=?", (event_id, session_id))
    conn.execute("DELETE FROM scribe_events WHERE id=? AND session_id=?", (event_id, session_id))
    conn.commit()
    return {"ok": True}