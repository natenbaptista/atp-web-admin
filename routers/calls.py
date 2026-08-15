"""
routers/calls.py — /calls/*

Ports: atp-dev/src/manager/app/Controller/CallsController.php

Routes:
  GET  /calls                   → call log index (filter form + results)
  GET  /calls/json              → JSON search for React frontend
  GET  /calls/csv               → streaming CSV download
  GET  /calls/pdf-report        → printable HTML report (open in new tab)
"""

import csv
import io
import pathlib
from datetime import datetime, timezone
from typing import Optional

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

import atp_client
from session import require_session
from logging_config import logger

router = APIRouter(prefix="/calls")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"

PAGE_SIZE = 25   # Must match C++ CallLogStore hardcoded limit

_DIRECTIONS = ["barge", "incoming", "outgoing", "parked", "forwarded"]
_STATUSES   = ["Received", "Missed", "Busy", "Initiated", "Blank"]


# ── Index ─────────────────────────────────────────────────────────────────────

@router.get("")
async def calls_index():
    return FileResponse(PUBLIC_DIR / "index.html")


# ── JSON search (for React frontend) ─────────────────────────────────────────

@router.get("/json", response_class=JSONResponse)
async def calls_json(
    request: Request,
    session: dict = Depends(require_session),
    start_date:   Optional[str] = None,
    end_date:     Optional[str] = None,
    owner:        Optional[str] = None,
    participants: Optional[str] = None,
    vri:          Optional[str] = None,
    line:         Optional[str] = None,
    cli:          Optional[str] = None,
    include_icm:  Optional[str] = None,
    page:         int = 1,
):
    # Frontend sends repeated "direction" and "status" params (singular)
    directions = request.query_params.getlist("direction")
    statuses   = request.query_params.getlist("status")

    # Resolve primary_timezone for date conversion (mirrors PHP date_default_timezone_get)
    tz_name = await _get_primary_tz()

    filters = _build_filters(
        start_date, end_date, owner, participants, vri, line,
        cli, include_icm, directions, statuses, page, tz_name,
    )

    raw_calls, total_pages = await _safe_call_search(filters)
    calls = [_normalize_call(c, tz_name) for c in raw_calls]

    return {"items": calls, "total": total_pages * PAGE_SIZE, "pages": total_pages}


# ── CSV export ────────────────────────────────────────────────────────────────

@router.get("/csv")
async def calls_csv(
    request: Request,
    session: dict = Depends(require_session),
    start_date:   Optional[str] = None,
    end_date:     Optional[str] = None,
    owner:        Optional[str] = None,
    participants: Optional[str] = None,
    vri:          Optional[str] = None,
    line:         Optional[str] = None,
    cli:          Optional[str] = None,
    include_icm:  Optional[str] = None,
    from_page:    int = 1,
    to_page:      int = 100,
):
    directions = request.query_params.getlist("direction")
    statuses   = request.query_params.getlist("status")
    tz_name    = await _get_primary_tz()

    calls_all: list[dict] = []
    for p in range(from_page, to_page + 1):
        filters = _build_filters(
            start_date, end_date, owner, participants, vri, line,
            cli, include_icm, directions, statuses, p, tz_name,
        )
        raw, total_pages = await _safe_call_search(filters)
        calls_all.extend(raw)
        if p >= total_pages:
            break

    calls = [_normalize_call(c, tz_name) for c in calls_all]
    logger.info("CSV export: %d calls by %r", len(calls), session.get("username"))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["VRI", "Begin Date", "Duration", "Owner",
                     "Participants", "Direction", "Lines", "CLI", "Status"])
    for c in calls:
        participants_str = ", ".join(c.get("participants") or []) if isinstance(c.get("participants"), list) else (c.get("participants") or "")
        lines_str = ", ".join(c.get("lines") or []) if isinstance(c.get("lines"), list) else (c.get("lines") or "")
        writer.writerow([
            c.get("vri", ""),
            c.get("start_time", ""),
            c.get("duration_str", ""),
            c.get("owner", ""),
            participants_str,
            c.get("direction", ""),
            lines_str,
            c.get("cli", ""),
            c.get("status", ""),
        ])

    output.seek(0)
    filename = "call_log_{}.csv".format(
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    )
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Printable HTML report ─────────────────────────────────────────────────────

@router.get("/pdf-report")
async def calls_pdf_report():
    return FileResponse(PUBLIC_DIR / "index.html")


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_primary_tz() -> str:
    """Read primary_timezone from ATP settings (same logic as recording.py)."""
    try:
        settings = await atp_client.settings_get()
        raw = settings.get("primary_timezone")
        if isinstance(raw, dict):
            raw = raw.get("value", "")
        if raw and isinstance(raw, str):
            return raw.strip()
    except Exception:
        pass
    return "UTC"


def _local_to_utc(dt_str: str, tz_name: str) -> str:
    """
    Convert a datetime string from primary_timezone to UTC for the C++ backend.
    Input format: "YYYY-MM-DDTHH:MM" or "YYYY-MM-DD HH:MM" (from datetime-local input).
    Output format: "YYYY-MM-DD HH:MM" (what C++ time_from_string expects).
    """
    if not dt_str:
        return ""
    # Normalise: replace T separator, strip seconds if present
    dt_str = dt_str.replace("T", " ").strip()
    # Parse
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            naive = datetime.strptime(dt_str, fmt)
            break
        except ValueError:
            continue
    else:
        return dt_str  # pass through unchanged if unparseable

    try:
        local_tz = ZoneInfo(tz_name)
        local_dt = naive.replace(tzinfo=local_tz)
        utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
        return utc_dt.strftime("%Y-%m-%d %H:%M")
    except (ZoneInfoNotFoundError, Exception):
        return naive.strftime("%Y-%m-%d %H:%M")


def _build_filters(
    start_date, end_date, owner, participants, vri, line,
    cli, include_icm, directions, statuses, page, tz_name,
) -> dict:
    filters: dict = {}

    # Dates: convert from primary_timezone → UTC (mirrors PHP behaviour)
    sd = _local_to_utc((start_date or "").strip(), tz_name)
    ed = _local_to_utc((end_date   or "").strip(), tz_name)
    if sd: filters["start_date"] = sd
    if ed: filters["end_date"]   = ed

    if (owner or "").strip():        filters["owner"]        = owner.strip()
    if (participants or "").strip(): filters["participants"] = participants.strip()
    if (vri or "").strip():          filters["vri"]          = vri.strip()
    if (line or "").strip():         filters["line"]         = line.strip()
    if (cli or "").strip():          filters["cli"]          = cli.strip()
    if include_icm == "1":           filters["include_icm"]  = True

    valid_dirs = [d for d in (directions or []) if d in _DIRECTIONS]
    valid_sts  = [s for s in (statuses   or []) if s in _STATUSES]
    if valid_dirs: filters["directions"] = valid_dirs
    if valid_sts:  filters["statuses"]   = valid_sts

    # C++ page is 0-based; no page field = returns first 25 only
    filters["page"] = max(0, int(page) - 1)

    return filters


def _duration_str_to_seconds(dur: str) -> int:
    """Convert 'HH:MM:SS' or 'H:MM:SS' string from C++ to integer seconds."""
    if not dur:
        return 0
    parts = dur.strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        pass
    return 0


def _parse_begin_utc(begin_str: str) -> Optional[datetime]:
    """
    Parse the UTC datetime string from C++ boost::posix_time::to_simple_string.
    Possible formats:
      "2026-Mar-20 10:06:11.832057"   (boost simple string — abbreviated month)
      "2026-03-20 10:06:11"           (ISO-ish, if backend ever changes)
      "20260320T100611"               (ISO compact)
    Returns a UTC-aware datetime, or None on failure.
    """
    if not begin_str:
        return None
    s = begin_str.strip()
    # boost to_simple_string: "YYYY-Mon-DD HH:MM:SS.ffffff"
    for fmt in (
        "%Y-%b-%d %H:%M:%S.%f",   # 2026-Mar-20 10:06:11.832057
        "%Y-%b-%d %H:%M:%S",      # 2026-Mar-20 10:06:11
        "%Y-%m-%d %H:%M:%S.%f",   # 2026-03-20 10:06:11.832057
        "%Y-%m-%d %H:%M:%S",      # 2026-03-20 10:06:11
        "%Y-%m-%d %H:%M",         # 2026-03-20 10:06
        "%Y%m%dT%H%M%S",          # 20260320T100611
    ):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=ZoneInfo("UTC"))
        except ValueError:
            continue
    return None


def _utc_to_local_str(begin_str: str, tz_name: str) -> str:
    """
    Convert C++ begin string (UTC) to primary_timezone, formatted as 'YYYY-MM-DD HH:MM:SS TZ'.
    Falls back to original string if parsing fails.
    """
    dt_utc = _parse_begin_utc(begin_str)
    if dt_utc is None:
        return begin_str
    try:
        local_tz = ZoneInfo(tz_name)
        dt_local = dt_utc.astimezone(local_tz)
        # Format: "2026-04-05 08:48:07 IST"  (matches PHP format 'Y-m-d H:i:s T')
        tz_abbr = dt_local.strftime("%Z")
        return dt_local.strftime("%Y-%m-%d %H:%M:%S") + " " + tz_abbr
    except (ZoneInfoNotFoundError, Exception):
        return begin_str


def _normalize_call(c: dict, tz_name: str = "UTC") -> dict:
    """
    Map C++ CallLogResult field names to what the frontend expects.
      begin       → start_time  (converted UTC → primary_timezone, formatted)
      progress    → status
      duration    → duration (int seconds) + duration_str (original string for CSV)
    """
    dur_str = c.get("duration", "")
    begin_raw = c.get("begin", c.get("start_time", ""))
    return {
        **c,
        "start_time":   _utc_to_local_str(begin_raw, tz_name),
        "status":       c.get("progress", c.get("status", "")),
        "duration":     _duration_str_to_seconds(dur_str),
        "duration_str": dur_str,
    }


async def _safe_call_search(filters: dict) -> tuple[list, int]:
    """Returns (records, total_pages). total_pages comes from the first result record."""
    try:
        records = await atp_client.call_log_search(filters)
        total_pages = 1
        if records:
            total_pages = max(1, int(records[0].get("total_pages", 1)))
        return records, total_pages
    except atp_client.AtpBackendError as exc:
        logger.error("Call search failed: %s", exc)
        return [], 1
