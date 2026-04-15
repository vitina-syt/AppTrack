"""
Utility endpoints  —  /api/util/*

GET /api/util/pick-file  — open a native OS file-picker dialog on the server
                           machine and return the selected absolute path.
GET /api/util/mic-check  — record 2 s of audio and return RMS level + device list.
"""
from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool

router = APIRouter(prefix="/api/util", tags=["util"])


@router.get("/pick-file")
async def pick_file(
    title:       str = Query(default="选择文件"),
    filter_name: str = Query(default="所有文件"),
    filter_ext:  str = Query(default="*.*"),
):
    """Open a native Windows file-picker dialog (tkinter) and return the path.

    Runs in a thread-pool so the async event loop is never blocked.
    Returns {"path": ""} if the user cancels.
    """
    def _open_dialog():
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()                        # hide the blank root window
            root.wm_attributes("-topmost", True)   # bring dialog to foreground
            path = filedialog.askopenfilename(
                title=title,
                filetypes=[(filter_name, filter_ext), ("所有文件", "*.*")],
            )
            root.destroy()
            return str(path) if path else ""
        except Exception:
            return ""

    path = await run_in_threadpool(_open_dialog)
    return {"path": path}


@router.get("/mic-check")
async def mic_check(
    duration_ms: int = Query(default=2000, ge=500, le=8000,
                             description="How long to record in milliseconds"),
):
    """
    Record a short audio clip and return a diagnostic report:
      - whether pyaudio is installed
      - list of available input devices with the default marked
      - peak RMS level of the captured audio (0-1 scale)
      - whether the level crossed the speech threshold
      - error message if anything failed

    This lets the frontend tell the user exactly why voice capture is broken.
    """
    def _run():
        report = {
            "pyaudio_available": False,
            "whisper_available": False,
            "devices":           [],
            "default_device":    None,
            "rms":               0.0,
            "peak":              0.0,
            "has_speech":        False,
            "silence_thresh":    0.01,
            "duration_ms":       duration_ms,
            "error":             None,
        }

        # ── 1. Check pyaudio ──────────────────────────────────────────────────
        try:
            import pyaudio
        except ImportError:
            report["error"] = "pyaudio 未安装 — 运行: pip install pyaudio"
            return report
        report["pyaudio_available"] = True

        # ── 2. Check whisper ──────────────────────────────────────────────────
        try:
            import whisper  # noqa: F401
            report["whisper_available"] = True
        except ImportError:
            try:
                import openai  # noqa: F401
                report["whisper_available"] = True   # API fallback available
            except ImportError:
                pass

        # ── 3. List input devices ─────────────────────────────────────────────
        pa = pyaudio.PyAudio()
        try:
            default_idx = None
            try:
                default_idx = pa.get_default_input_device_info()["index"]
            except Exception:
                pass
            report["default_device"] = default_idx

            for i in range(pa.get_device_count()):
                try:
                    info = pa.get_device_info_by_index(i)
                    if info.get("maxInputChannels", 0) > 0:
                        report["devices"].append({
                            "index":       i,
                            "name":        info.get("name", f"Device {i}"),
                            "channels":    info.get("maxInputChannels"),
                            "sample_rate": int(info.get("defaultSampleRate", 0)),
                            "default":     (i == default_idx),
                        })
                except Exception:
                    pass
        except Exception as exc:
            report["error"] = f"设备枚举失败: {exc}"
            pa.terminate()
            return report

        if not report["devices"]:
            report["error"] = "未找到任何麦克风输入设备"
            pa.terminate()
            return report

        # ── 4. Record a short clip and measure RMS ────────────────────────────
        RATE    = 16000
        CHUNK   = 1024
        SILENCE_THRESH = 0.01
        report["silence_thresh"] = SILENCE_THRESH
        n_frames = int(RATE / CHUNK * (duration_ms / 1000))

        try:
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK,
            )
            frames = []
            for _ in range(n_frames):
                frames.append(stream.read(CHUNK, exception_on_overflow=False))
            stream.stop_stream()
            stream.close()

            raw = b"".join(frames)

            # RMS and peak using numpy if available, else fallback
            try:
                import numpy as np
                pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                rms  = float(np.sqrt(np.mean(pcm ** 2)))
                peak = float(np.max(np.abs(pcm)))
            except ImportError:
                # Manual RMS from 16-bit samples
                import struct
                samples = struct.unpack(f"{len(raw)//2}h", raw)
                rms  = (sum(s * s for s in samples) / len(samples)) ** 0.5 / 32768.0
                peak = max(abs(s) for s in samples) / 32768.0

            report["rms"]        = round(rms, 5)
            report["peak"]       = round(peak, 5)
            report["has_speech"] = rms >= SILENCE_THRESH

        except Exception as exc:
            report["error"] = f"录音失败: {exc}"
            pa.terminate()
            return report

        pa.terminate()
        return report

    return await run_in_threadpool(_run)
