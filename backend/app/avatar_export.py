"""
Avatar Export — generate a digital-presenter video via HeyGen or D-ID.

Usage
-----
    result = export_avatar(
        narration_text = "In this step, you will...",
        provider       = "heygen",          # "heygen" | "did"
        avatar_id      = "...",             # provider-specific avatar ID
        voice_id       = "...",             # provider-specific voice ID
        api_key        = "...",             # or set HEYGEN_API_KEY / DID_API_KEY env var
    )
    # result = {"job_id": "...", "status": "processing", "video_url": None}

    status = poll_avatar_job(job_id, provider, api_key)
    # status = {"status": "done", "video_url": "https://..."}

Both functions are synchronous (run them in a thread-pool from async routes).
"""
import os
import time
import logging
from typing import Optional

logger = logging.getLogger("app.avatar_export")

try:
    import httpx
    _HTTPX = True
except ImportError:
    _HTTPX = False
    logger.info("httpx not available — avatar export disabled")


# ── HeyGen ────────────────────────────────────────────────────────────────────

_HEYGEN_BASE = "https://api.heygen.com"


def _heygen_create(
    narration: str,
    avatar_id: str,
    voice_id: str,
    api_key: str,
) -> dict:
    """
    POST /v2/video/generate to HeyGen.
    Returns {"job_id": "...", "status": "processing", "video_url": None}.
    """
    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": avatar_id,
                    "avatar_style": "normal",
                },
                "voice": {
                    "type": "text",
                    "input_text": narration,
                    "voice_id": voice_id,
                    "speed": 1.0,
                },
                "background": {"type": "color", "value": "#FAFAFA"},
            }
        ],
        "dimension": {"width": 1280, "height": 720},
        "aspect_ratio": None,
        "test": False,
    }
    resp = httpx.post(
        f"{_HEYGEN_BASE}/v2/video/generate",
        headers={
            "X-Api-Key": api_key,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    return {
        "job_id": data.get("video_id", ""),
        "status": "processing",
        "video_url": None,
    }


def _heygen_poll(job_id: str, api_key: str) -> dict:
    """
    GET /v1/video_status.get?video_id=... from HeyGen.
    Returns {"status": "processing|done|error", "video_url": str|None}.
    """
    resp = httpx.get(
        f"{_HEYGEN_BASE}/v1/video_status.get",
        params={"video_id": job_id},
        headers={"X-Api-Key": api_key},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    heygen_status = data.get("status", "processing")  # processing|completed|failed

    status_map = {"completed": "done", "failed": "error"}
    status = status_map.get(heygen_status, "processing")
    return {
        "status": status,
        "video_url": data.get("video_url") or None,
    }


# ── D-ID ─────────────────────────────────────────────────────────────────────

_DID_BASE = "https://api.d-id.com"


def _did_create(
    narration: str,
    avatar_id: str,   # for D-ID this is the source_url of the avatar image
    voice_id: str,    # D-ID voice_id string
    api_key: str,
) -> dict:
    """
    POST /talks to D-ID.
    Returns {"job_id": "...", "status": "processing", "video_url": None}.
    """
    payload = {
        "script": {
            "type": "text",
            "input": narration,
            "provider": {
                "type": "microsoft",
                "voice_id": voice_id or "en-US-JennyNeural",
            },
        },
        "source_url": avatar_id,   # URL of the avatar image or D-ID presenter ID
        "config": {"fluent": True, "pad_audio": 0.0},
    }
    resp = httpx.post(
        f"{_DID_BASE}/talks",
        auth=(api_key, ""),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "job_id": data.get("id", ""),
        "status": "processing",
        "video_url": None,
    }


def _did_poll(job_id: str, api_key: str) -> dict:
    """
    GET /talks/{id} from D-ID.
    Returns {"status": "processing|done|error", "video_url": str|None}.
    """
    resp = httpx.get(
        f"{_DID_BASE}/talks/{job_id}",
        auth=(api_key, ""),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    did_status = data.get("status", "created")  # created|started|done|error

    status_map = {"done": "done", "error": "error"}
    status = status_map.get(did_status, "processing")
    return {
        "status": status,
        "video_url": data.get("result_url") or None,
    }


# ── Public functions ──────────────────────────────────────────────────────────

def export_avatar(
    narration_text: str,
    provider: str = "heygen",
    avatar_id: str = "",
    voice_id: str = "",
    api_key: Optional[str] = None,
) -> dict:
    """
    Submit a narration to HeyGen or D-ID for avatar video generation.

    Parameters
    ----------
    narration_text : str
        The script text (narration generated by Claude).
    provider : str
        "heygen" or "did".
    avatar_id : str
        HeyGen: avatar_id string from your HeyGen account.
        D-ID: source_url of the presenter image.
    voice_id : str
        Provider-specific voice ID.
    api_key : str | None
        Falls back to HEYGEN_API_KEY or DID_API_KEY env vars.

    Returns
    -------
    dict with keys: job_id, status, video_url
    """
    if not _HTTPX:
        raise RuntimeError("httpx is required for avatar export. Run: pip install httpx")

    if not narration_text.strip():
        raise ValueError("narration_text cannot be empty")

    provider = provider.lower()

    if provider == "heygen":
        key = api_key or os.environ.get("HEYGEN_API_KEY", "")
        if not key:
            raise ValueError("HeyGen API key required (set HEYGEN_API_KEY env var)")
        aid = avatar_id or os.environ.get("HEYGEN_AVATAR_ID", "Daisy-inskirt-20220818")
        vid = voice_id  or os.environ.get("HEYGEN_VOICE_ID",  "1bd001e7e50f421d891986aad5158bc8")
        return _heygen_create(narration_text, aid, vid, key)

    elif provider == "did":
        key = api_key or os.environ.get("DID_API_KEY", "")
        if not key:
            raise ValueError("D-ID API key required (set DID_API_KEY env var)")
        aid = avatar_id or os.environ.get(
            "DID_AVATAR_URL",
            "https://create-images-results.d-id.com/DefaultPresenters/Noelle_f/image.jpeg",
        )
        vid = voice_id or os.environ.get("DID_VOICE_ID", "en-US-JennyNeural")
        return _did_create(narration_text, aid, vid, key)

    else:
        raise ValueError(f"Unknown provider '{provider}'. Choose 'heygen' or 'did'.")


def poll_avatar_job(
    job_id: str,
    provider: str = "heygen",
    api_key: Optional[str] = None,
) -> dict:
    """
    Poll a previously submitted avatar job for completion.

    Returns
    -------
    dict: {"status": "processing|done|error", "video_url": str|None}
    """
    if not _HTTPX:
        raise RuntimeError("httpx is required for avatar export")

    provider = provider.lower()

    if provider == "heygen":
        key = api_key or os.environ.get("HEYGEN_API_KEY", "")
        return _heygen_poll(job_id, key)
    elif provider == "did":
        key = api_key or os.environ.get("DID_API_KEY", "")
        return _did_poll(job_id, key)
    else:
        raise ValueError(f"Unknown provider '{provider}'")


def poll_until_done(
    job_id: str,
    provider: str = "heygen",
    api_key: Optional[str] = None,
    timeout_secs: int = 600,
    poll_interval: int = 10,
) -> dict:
    """
    Block until the avatar job is done or timeout expires.
    Returns final status dict.
    """
    deadline = time.time() + timeout_secs
    while time.time() < deadline:
        result = poll_avatar_job(job_id, provider, api_key)
        if result["status"] in ("done", "error"):
            return result
        time.sleep(poll_interval)
    return {"status": "timeout", "video_url": None}