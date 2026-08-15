"""
routers/docs.py — /docs/*

Ports: atp-dev/src/manager/app/Controller/DocsController.php

Serves Markdown documentation files from the atp-dev-24/doc/ directory,
rendered to HTML via python-markdown.

Routes:
  GET  /docs             → redirect to /docs/admin
  GET  /docs/admin       → administration section index
  GET  /docs/dev         → developer section index
  GET  /docs/{section}/{path:path}  → render a doc file or directory index
"""

import pathlib
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse

from session import require_session
from logging_config import logger

router = APIRouter(prefix="/docs")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"


@router.get("")
async def docs_root(session: dict = Depends(require_session)):
    return RedirectResponse(url="/docs/admin")


@router.get("/{section}")
async def docs_section(
    section: str,
    session: dict = Depends(require_session),
):
    return FileResponse(PUBLIC_DIR / "index.html")


@router.get("/{section}/{path:path}")
async def docs_display(
    section: str,
    path: str,
    session: dict = Depends(require_session),
):
    return FileResponse(PUBLIC_DIR / "index.html")
