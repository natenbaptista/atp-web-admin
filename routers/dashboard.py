"""
routers/dashboard.py — GET /dashboard

Shows system summary: user count, line count, backend status, recent audit entries.
Data is fetched concurrently with asyncio.gather() to minimise page load time.
"""

import pathlib
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse

from session import get_session

router = APIRouter()

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"


@router.get("/dashboard")
async def dashboard(
    session: Optional[dict] = Depends(get_session),
):
    if not session:
        return RedirectResponse(url="/login", status_code=303)

    return FileResponse(PUBLIC_DIR / "index.html")
