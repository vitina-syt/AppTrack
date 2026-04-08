"""Usage statistics endpoints."""
from fastapi import APIRouter, Path
from app.database import get_conn
from app.models import AppStat

router = APIRouter(prefix="/api/stats", tags=["stats"])

_STAT_SQL = """
    SELECT app_name,
           MAX(exe_path) AS exe_path,
           SUM(
               CASE WHEN ended_at IS NOT NULL THEN duration_seconds
                    ELSE CAST((julianday('now') - julianday(started_at)) * 86400 AS INTEGER)
               END
           ) AS total_seconds,
           COUNT(*) AS session_count
    FROM sessions
    WHERE date(started_at, 'localtime') = ?
    GROUP BY app_name
    ORDER BY total_seconds DESC
    LIMIT 50
"""


@router.get("/today", response_model=list[AppStat])
def stats_today():
    conn = get_conn()
    rows = conn.execute(_STAT_SQL, ("now",)).fetchall()
    return [dict(r) for r in rows]


@router.get("/date/{date}", response_model=list[AppStat])
def stats_for_date(date: str = Path(description="YYYY-MM-DD")):
    conn = get_conn()
    rows = conn.execute(_STAT_SQL, (date,)).fetchall()
    return [dict(r) for r in rows]


@router.get("/days", response_model=list[dict])
def active_days(limit: int = 30):
    """Return dates that have recorded sessions, newest first."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT date(started_at, 'localtime') AS day,
                  COUNT(*) AS session_count,
                  SUM(duration_seconds) AS total_seconds
           FROM sessions
           WHERE ended_at IS NOT NULL
           GROUP BY day
           ORDER BY day DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
