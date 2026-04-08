"""Tracker control endpoints."""
from fastapi import APIRouter, Query
from app.tracker import tracker
from app.models import TrackerStatus

router = APIRouter(prefix="/api/tracker", tags=["tracker"])


@router.get("/status", response_model=TrackerStatus)
def get_status():
    return tracker.status


@router.post("/start")
def start_tracking(poll_interval: int = Query(default=5, ge=1, le=60)):
    tracker.start(poll_interval=poll_interval)
    return {"ok": True}


@router.post("/stop")
def stop_tracking():
    tracker.stop()
    return {"ok": True}
