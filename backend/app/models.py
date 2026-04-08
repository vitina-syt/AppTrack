"""Pydantic response models."""
from pydantic import BaseModel
from typing import Optional


class Session(BaseModel):
    id: int
    app_name: str
    exe_path: Optional[str]
    window_title: Optional[str]
    started_at: str
    ended_at: Optional[str]
    duration_seconds: int


class AppStat(BaseModel):
    app_name: str
    exe_path: Optional[str]
    total_seconds: int
    session_count: int


class TrackerStatus(BaseModel):
    running: bool
    current_app: Optional[str]
    current_exe: Optional[str]
    current_title: Optional[str]
    poll_interval: int


# ── Screen Recording ─────────────────────────────────────────────────────────

class RecordingBase(BaseModel):
    title: str
    note: str


class Recording(RecordingBase):
    id: int
    started_at: str
    ended_at: Optional[str]
    screenshot_dir: str
    event_count: Optional[int] = None   # populated by list endpoint


class RecordingUpdate(BaseModel):
    title: Optional[str] = None
    note: Optional[str] = None


class Event(BaseModel):
    id: int
    recording_id: int
    seq: int
    event_type: str          # click | scroll | app_open | screenshot | key
    timestamp: str
    app_name: Optional[str]
    window_title: Optional[str]
    x: Optional[int]
    y: Optional[int]
    button: Optional[str]
    scroll_dx: Optional[int]
    scroll_dy: Optional[int]
    screenshot_path: Optional[str]
    annotation: str


class EventAnnotationUpdate(BaseModel):
    annotation: str


class RecorderStatus(BaseModel):
    running: bool
    recording_id: Optional[int]
    events_captured: int


# ── CreoScribe ───────────────────────────────────────────────────────────────

class ScribeSession(BaseModel):
    id: int
    title: str
    target_app: str
    started_at: str
    ended_at: Optional[str]
    status: str                       # recording|processing|done|error
    narration_text: Optional[str]
    avatar_video_url: Optional[str]
    avatar_job_id: Optional[str]
    screenshot_dir: str
    error_message: Optional[str]
    event_count: Optional[int] = None


class ScribeSessionUpdate(BaseModel):
    title: Optional[str] = None
    narration_text: Optional[str] = None


class ScribeEvent(BaseModel):
    id: int
    session_id: int
    seq: int
    event_type: str
    timestamp: str
    app_name: Optional[str]
    window_title: Optional[str]
    uia_element_name: Optional[str]
    uia_element_type: Optional[str]
    uia_automation_id: Optional[str]
    screenshot_path: Optional[str]
    voice_text: Optional[str]
    voice_confidence: Optional[float]
    annotation: str


class ScribeAgentStatus(BaseModel):
    running: bool
    session_id: Optional[int]
    events_captured: int
    voice_segments: int
    uia_events: int
