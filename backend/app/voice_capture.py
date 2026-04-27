"""
Voice Capture — microphone recording with Azure OpenAI Whisper transcription.

Captures microphone audio in chunks, detects silence, and transcribes each
speech segment via the Azure OpenAI Whisper API (auto language detection).

Requires:
    AZURE_WHISPER_API_KEY  environment variable with the Azure API key.

Public API
----------
    vc = VoiceCapture()
    vc.start(callback)          # callback({"voice_text": "...", "timestamp": ...})
    vc.stop()
    segments = vc.drain()       # list of all transcribed segments
"""
import collections
import threading
import logging
import queue
import os
import tempfile
from datetime import datetime, timezone
from typing import Optional, Callable, List
from pathlib import Path

logger = logging.getLogger("app.voice_capture")

# Azure OpenAI Whisper endpoint
_AZURE_WHISPER_ENDPOINT = (
    "https://oai-seaidev-concept-advisor.cognitiveservices.azure.com"
    "/openai/deployments/whisper/audio/transcriptions"
    "?api-version=2024-06-01"
)

# ── optional pyaudio ─────────────────────────────────────────────────────────
_PYAUDIO = False
try:
    import pyaudio
    _PYAUDIO = True
except ImportError:
    logger.info("pyaudio not available — voice capture disabled")

# ── optional numpy (for RMS silence detection + audio normalisation) ─────────
_NUMPY = False
try:
    import numpy as np
    _NUMPY = True
except ImportError:
    pass


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_audio(raw: bytes, target_rms: float = 0.08) -> bytes:
    """Normalise PCM audio to *target_rms* so quiet speech is amplified before
    Whisper sees it.  Applies a soft gain cap (10×) to avoid clipping noise."""
    if not _NUMPY or not raw:
        return raw
    import numpy as np
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    current_rms = float(np.sqrt(np.mean(pcm ** 2)))
    if current_rms < 1e-6:
        return raw
    gain = min(target_rms / current_rms, 10.0)
    normalised = np.clip(pcm * gain, -1.0, 1.0)
    return (normalised * 32767).astype(np.int16).tobytes()


def _transcribe_file(wav_path: str) -> tuple[str, float]:
    """Transcribe a WAV file via Azure OpenAI Whisper API.

    Returns (text, confidence).  Language is auto-detected by the service.
    Requires AZURE_WHISPER_API_KEY environment variable.
    """
    azure_key = os.environ.get("AZURE_WHISPER_API_KEY", "")
    if not azure_key:
        logger.warning(
            "AZURE_WHISPER_API_KEY not set — voice transcription disabled. "
            "Add it to your .env file."
        )
        return "", 0.0

    try:
        import httpx
        with open(wav_path, "rb") as f:
            resp = httpx.post(
                _AZURE_WHISPER_ENDPOINT,
                headers={"api-key": azure_key},
                files={"file": ("audio.wav", f, "audio/wav")},
                timeout=60,
            )
        resp.raise_for_status()
        text = resp.json().get("text", "").strip()
        logger.debug("Azure Whisper: text=%r", text[:80])
        return text, 0.95
    except Exception as exc:
        logger.warning("Azure Whisper API failed: %s", exc)
        return "", 0.0


# ── VoiceCapture ─────────────────────────────────────────────────────────────

class VoiceCapture:
    """
    Capture microphone audio and transcribe via Azure OpenAI Whisper.

    Parameters
    ----------
    silence_thresh  : float — RMS below this is considered silence (0-1 scale)
    silence_secs    : float — seconds of silence to end a speech segment
    max_segment_secs: float — force-flush segment after this duration
    audio_dir       : Path  — where to save raw WAV segments (None = temp dir)
    """

    RATE     = 16000
    CHUNK    = 1024
    CHANNELS = 1
    FORMAT   = None  # set in __init__ if pyaudio available

    def __init__(
        self,
        silence_thresh: float = 0.01,
        silence_secs: float = 1.5,
        max_segment_secs: float = 30.0,
        audio_dir: Optional[Path] = None,
    ):
        self.silence_thresh   = silence_thresh
        self.silence_secs     = silence_secs
        self.max_segment_secs = max_segment_secs
        self.audio_dir        = audio_dir

        self._lock            = threading.Lock()
        self._running         = False
        self._capture_thread: Optional[threading.Thread] = None
        self._transcribe_thread: Optional[threading.Thread] = None
        self._segment_queue: queue.Queue = queue.Queue()
        self._segments: List[dict] = []
        self._callback: Optional[Callable] = None

        # Rolling PCM buffer — keeps the last 60 s of raw audio for per-frame snapshots
        self._pcm_buffer: collections.deque = collections.deque()
        self._pcm_lock   = threading.Lock()
        self._pcm_max_chunks = int(60 * self.RATE / self.CHUNK)

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

    def snapshot_pcm(self, window_secs: float = 15.0) -> bytes:
        """Return the last *window_secs* of raw PCM audio (16 kHz, mono, int16).

        Called at screenshot-trigger time for per-frame voice alignment.
        Returns b'' if buffer is empty or audio is pure silence.
        """
        frames_needed = max(1, int(window_secs * self.RATE / self.CHUNK))
        with self._pcm_lock:
            recent = list(self._pcm_buffer)[-frames_needed:]
        if not recent:
            return b""
        raw = b"".join(recent)
        if _NUMPY:
            import numpy as np
            pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            n_chunks = len(pcm) // self.CHUNK
            if n_chunks > 0:
                chunks = pcm[:n_chunks * self.CHUNK].reshape(n_chunks, self.CHUNK)
                max_chunk_rms = float(np.max(np.sqrt(np.mean(chunks ** 2, axis=1))))
                if max_chunk_rms < self.silence_thresh:
                    return b""
        return raw

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    # ── capture loop ─────────────────────────────────────────────────────

    def _capture_loop(self) -> None:
        pa = pyaudio.PyAudio()
        stream = None
        try:
            stream = pa.open(
                format=self.FORMAT,
                channels=self.CHANNELS,
                rate=self.RATE,
                input=True,
                frames_per_buffer=self.CHUNK,
            )
        except Exception as exc:
            logger.warning("Voice capture: failed to open audio stream (%s) — recording disabled", exc)
            pa.terminate()
            with self._lock:
                self._running = False
            return

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

                with self._pcm_lock:
                    self._pcm_buffer.append(data)
                    if len(self._pcm_buffer) > self._pcm_max_chunks:
                        self._pcm_buffer.popleft()

                if _NUMPY:
                    pcm = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                    rms = float(np.sqrt(np.mean(pcm ** 2)))
                else:
                    rms = 0.05

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

            if in_speech and frames_in_segment:
                self._flush_segment(frames_in_segment, seg_start_ts)

        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()

    def _flush_segment(self, frames: List[bytes], start_ts: str) -> None:
        """Normalise and write audio frames to a WAV file, then enqueue for transcription."""
        import wave

        raw = _normalize_audio(b"".join(frames))

        out_dir = self.audio_dir or Path(tempfile.gettempdir())
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        wav_path = out_dir / f"voice_{ts}.wav"

        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(self.CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(self.RATE)
            wf.writeframes(raw)

        self._segment_queue.put((str(wav_path), start_ts))

    # ── transcribe loop ───────────────────────────────────────────────────

    def _transcribe_loop(self) -> None:
        while True:
            item = self._segment_queue.get()
            if item is None:
                break
            wav_path, start_ts = item
            try:
                text, confidence = _transcribe_file(wav_path)
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
                try:
                    Path(wav_path).unlink(missing_ok=True)
                except Exception:
                    pass
