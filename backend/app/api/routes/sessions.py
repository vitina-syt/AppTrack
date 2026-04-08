"""Session list endpoint."""
from fastapi import APIRouter, Query
from typing import Optional
from app.database import get_conn
from app.models import Session

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=list[Session])
def list_sessions(
    date: Optional[str] = Query(default=None, description="YYYY-MM-DD (local)"),
    limit: int = Query(default=200, ge=1, le=1000),
):
    conn = get_conn()
    if date:
        rows = conn.execute(
            """SELECT id, app_name, exe_path, window_title, started_at, ended_at,
                      CASE WHEN ended_at IS NOT NULL THEN duration_seconds
                           ELSE CAST((julianday('now') - julianday(started_at)) * 86400 AS INTEGER)
                      END AS duration_seconds
               FROM sessions
               WHERE date(started_at, 'localtime') = ?
               ORDER BY started_at DESC
               LIMIT ?""",
            (date, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, app_name, exe_path, window_title, started_at, ended_at,
                      CASE WHEN ended_at IS NOT NULL THEN duration_seconds
                           ELSE CAST((julianday('now') - julianday(started_at)) * 86400 AS INTEGER)
                      END AS duration_seconds
               FROM sessions
               ORDER BY started_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
