"""
routers/audit.py — /audit/*

Ports: atp-dev/src/manager/app/Controller/AuditController.php

Routes:
  GET  /audit          → audit log viewer (date filter, pagination)
"""

import pathlib
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse

import atp_client
from session import require_session, wants_json
from logging_config import logger

router = APIRouter(prefix="/audit")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"

_DEFAULT_LIMIT = 50


def _parse_time(time_str: str) -> str:
    """Convert Boost ISO string '20150206T085901' → '2015-02-06 08:59:01'."""
    try:
        return datetime.strptime(time_str, "%Y%m%dT%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return time_str or ""


def _format_entry(e: dict) -> dict:
    """Normalise a raw C++ LogRecord dict to the fields the React page expects."""
    session  = e.get("session") or {}
    username = session.get("username") or e.get("username") or ""

    parts: list[str] = []
    if e.get("model"):
        parts.append(e["model"])
    if e.get("field_name"):
        parts.append(f"field={e['field_name']}")
    if e.get("value_old") or e.get("value_new"):
        parts.append(f"{e.get('value_old', '')} → {e.get('value_new', '')}")
    if e.get("record_id"):
        parts.append(f"id={e['record_id']}")

    return {
        "id":        e.get("id", 0),
        "timestamp": _parse_time(e.get("time", "")),
        "username":  username,
        "action":    e.get("operation", ""),
        "details":   " | ".join(parts),
    }


# ── Index ─────────────────────────────────────────────────────────────────────

@router.get("")
async def audit_index(
    request: Request,
    limit: int = _DEFAULT_LIMIT,
    after_date:  Optional[str] = None,
    before_date: Optional[str] = None,
    after:  Optional[int] = None,   # ID-based cursor (matches C++ AuditSearchParam)
    before: Optional[int] = None,   # ID-based cursor
    session: dict = Depends(require_session),
):
    """Return audit logs as JSON or HTML page."""
    if wants_json(request):
        try:
            params: dict = {"limit": limit}
            if after_date:         params["after_date"]  = after_date
            if before_date:        params["before_date"] = before_date
            if after  is not None: params["after"]  = after
            if before is not None: params["before"] = before

            result = await atp_client.audit_search(params)
            audits = [_format_entry(e) for e in result["data"]]
            return JSONResponse({
                "audits":    audits,
                "has_newer": result["has_newer"],
                "has_older": result["has_older"],
            })
        except atp_client.AtpBackendError as exc:
            logger.error("Audit search failed: %s", exc)
            return JSONResponse({"audits": [], "has_newer": False, "has_older": False}, status_code=502)

    return FileResponse(PUBLIC_DIR / "index.html")
