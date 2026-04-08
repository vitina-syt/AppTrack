"""
Voice Capture — microphone recording with Whisper transcription.

Continuously captures microphone audio in chunks, detects silence,
and transcribes each speech segment via:
  1. openai-whisper  (local model, preferred)
  2. OpenAI Whisper API (if OPENAI_API_KEY is set and local model unavailable)
  3. No-op fallback  (saves audio only)

Public API
----------
    vc = VoiceCapture()
    vc.start(callback)          # callback({"voice_text": "...", "timestamp": ...})
    vc.stop()
    segments = vc.drain()       # list of all transcribed segments
"""
import threading
import time
import logging
import queue
import os
import tempfile
from datetime import datetime, timezone
from typing import Optional, Callable, List
from pathlib import Path

logger = logging.getLogger("app.voice_capture")

# ── optional pyaudio ─────────────────────────────────────────────────────────
_PYAUDIO = False
try:
    import pyaudio
    _PYAUDIO = True
except ImportError:
    logger.info("pyaudio not available — voice capture disabled")

# ── optional whisper (local) ─────────────────────────────────────────────────
_WHISPER_LOCAL = False
_whisper_model = None
try:
    import whisper as _whisper_lib
    _WHISPER_LOCAL = True
except ImportError:
    pass

# ── optional numpy (needed by whisper) ──────────────────────────────────────
_NUMPY = False
try:
    import numpy as np
    _NUMPY = True
except ImportError:
    pass


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_whisper(model_size: str = "base"):
    """Lazy-load Whisper model (called once on first use)."""
    global _whisper_model
    if _whisper_model is None and _WHISPER_LOCAL:
        logger.info("Loading Whisper model '%s' …", model_size)
        _whisper_model = _whisper_lib.load_model(model_size)
        logger.info("Whisper model loaded")
    return _whisper_model


def _transcribe_file(wav_path: str, model_size: str = "base") -> tuple[str, float]:
    """
    Transcribe an audio file. Returns (text, confidence).
    Tries local Whisper, then OpenAI API, then returns ("", 0.0).
    """
    # Local Whisper
    if _WHISPER_LOCAL and _NUMPY:
        try:
            model = _load_whisper(model_size)
            result = model.transcribe(wav_path, language=None, fp16=False)
            text = result.get("text", "").strip()
            # Average log-prob as a rough confidence proxy
            segs = result.get("segments", [])
            if segs:
                avg_lp = sum(s.get("avg_logprob", -1.0) for s in segs) / len(segs)
                confidence = max(0.0, min(1.0, (avg_lp + 1.0)))
            else:
                confidence = 0.5
            return text, confidence
        except Exception as exc:
            logger.warning("Local Whisper failed: %s", exc)

    # OpenAI API fallback
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if api_key:
        try:
            import httpx
            with open(wav_path, "rb") as f:
                resp = httpx.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": ("audio.wav", f, "audio/wav")},
                    data={"model": "whisper-1"},
                    timeout=30,
                )
            resp.raise_for_status()
            return resp.json().get("text", "").strip(), 0.8
        except Exception as exc:
            logger.warning("OpenAI Whisper API failed: %s", exc)

    return "", 0.0


# ── VoiceCapture ─────────────────────────────────────────────────────────────

class VoiceCapture:
    """
    Capture microphone audio and produce transcribed text segments.

    Parameters
    ----------
    sample_rate     : int   — audio sample rate (Hz)
    chunk_frames    : int   — frames per pyaudio read call
    silence_thresh  : float — RMS below this is considered silence (0-1 scale)
    silence_secs    : float — seconds of silence to end a speech segment
    max_segment_secs: float — force-flush segment after this duration
    whisper_model   : str   — Whisper model size (tiny/base/small/medium/large)
    audio_dir       : Path  — where to save raw WAV segments (None = temp dir)
    """

    RATE         = 16000
    CHUNK        = 1024
    CHANNELS     = 1
    FORMAT       = None  # set in __init__ if pyaudio available

    def __init__(
        self,
        silence_thresh: float = 0.01,
        silence_secs: float = 1.5,
        max_segment_secs: float = 30.0,
        whisper_model: str = "base",
        audio_dir: Optional[Path] = None,
    ):
        self.silence_thresh  = silence_thresh
        self.silence_secs    = silence_secs
        self.max_segment_secs = max_segment_secs
        self.whisper_model   = whisper_model
        self.audio_dir       = audio_dir

        self._lock            = threading.Lock()
        self._running         = False
        self._capture_thread: Optional[threading.Thread] = None
        self._transcribe_thread: Optional[threading.Thread] = None
        self._audio_queue: queue.Queue = queue.Queue()   # raw audio chunks
        self._segment_queue: queue.Queue = queue.Queue() # completed wav paths
        self._segments: List[dict] = []
        self._callback: Optional[Callable] = None

        if _PYAUDIO:
            self.FORMAT = pyaudio.paInt16

    # ── public ───────────────────────────────────────────────────────────

    def start(self, callback: Optional[Callable[[dict], None]] = None) -> None:
        """Start capture. callback(segment_dict) is called for each transcription."""
        with self._lock:
            if self._running:
                return
            self._callback = callback
            self._running = True
            self._segments = []

        if not _PYAUDIO:
            logger.warning("pyaudio unavailable — voice capture inactive")
            return

        self._capture_thread = threading.Thread(
            target=self._capture_loop, name="VoiceCapture", daemon=True
        )
        self._transcribe_thread = threading.Thread(
            target=self._transcribe_loop, name="VoiceTranscribe", daemon=True
        )
        self._capture_thread.start()
        self._transcribe_thread.start()
        logger.info("Voice capture started")

    def stop(self) -> None:
        with self._lock:
            self._running = False
        # Wake up transcribe loop
        self._segment_queue.put(None)
        if self._capture_thread:
            self._capture_thread.join(timeout=5)
        if self._transcribe_thread:
            self._transcribe_thread.join(timeout=10)
        logger.info("Voice capture stopped (%d segments)", len(self._segments))

    def drain(self) -> List[dict]:
        """Return all transcribed segments collected so far."""
        with self._lock:
            return list(self._segments)

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    # ── capture loop ─────────────────────────────────────────────────────

    def _capture_loop(self) -> None:
        pa = pyaudio.PyAudio()
        stream = pa.open(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.RATE,
            input=True,
            frames_per_buffer=self.CHUNK,
        )

        frames_in_segment: List[bytes] = []
        silent_frames = 0
        frames_per_second = self.RATE / self.CHUNK
        silence_limit = int(self.silence_secs * frames_per_second)
        max_frames = int(self.max_segment_secs * frames_per_second)
        in_speech = False
        seg_start_ts = _utcnow()

        try:
            while True:
                with self._lock:
                    if not self._running:
                        break

                data = stream.read(self.CHUNK, exception_on_overflow=False)
                frames_in_segment.append(data)

                # RMS energy check
                if _NUMPY:
                    pcm = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                    rms = float(np.sqrt(np.mean(pcm ** 2)))
                else:
                    rms = 0.05  # assume speaking if no numpy

                is_silent = rms < self.silence_thresh

                if not is_silent:
                    if not in_speech:
                        in_speech = True
                        seg_start_ts = _utcnow()
                    silent_frames = 0
                else:
                    if in_speech:
                        silent_frames += 1

                flush = (
                    (in_speech and silent_frames >= silence_limit) or
                    len(frames_in_segment) >= max_frames
                )

                if flush and in_speech:
                    self._flush_segment(frames_in_segment, seg_start_ts)
                    frames_in_segment = []
                    silent_frames = 0
                    in_speech = False

            # Flush remainder
            if in_speech and frames_in_segment:
                self._flush_segment(frames_in_segment, seg_start_ts)

        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()

    def _flush_segment(self, frames: List[bytes], start_ts: str) -> None:
        """Write audio frames to a WAV file and enqueue for transcription."""
        import wave

        out_dir = self.audio_dir or Path(tempfile.gettempdir())
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        wav_path = out_dir / f"voice_{ts}.wav"

        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(self.CHANNELS)
            wf.setsampwidth(2)  # paInt16 = 2 bytes
            wf.setframerate(self.RATE)
            wf.writeframes(b"".join(frames))

        self._segment_queue.put((str(wav_path), start_ts))

    # ── transcribe loop ───────────────────────────────────────────────────

    def _transcribe_loop(self) -> None:
        while True:
            item = self._segment_queue.get()
            if item is None:
                break
            wav_path, start_ts = item
            try:
                text, confidence = _transcribe_file(wav_path, self.whisper_model)
                if text:
                    seg = {
                        "event_type": "voice_segment",
                        "timestamp": start_ts,
                        "voice_text": text,
                        "voice_confidence": round(confidence, 3),
                    }
                    with self._lock:
                        self._segments.append(seg)
                    if self._callback:
                        try:
                            self._callback(seg)
                        except Exception as exc:
                            logger.debug("Voice callback error: %s", exc)
            except Exception as exc:
                logger.warning("Transcription failed for %s: %s", wav_path, exc)
            finally:
                # Clean up temp WAV
                try:
                    Path(wav_path).unlink(missing_ok=True)
                except Exception:
                    pass