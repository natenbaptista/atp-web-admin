"""
routers/backup.py — /backup/* and /restore/*

Ports: atp-dev/src/manager/app/Controller/BackupsController.php
       atp-dev/src/manager/app/Controller/RestoresController.php

Routes:
  GET  /backup                    → combined backup + restore page
  POST /backup/create             → AJAX: create backup → JSON {result, msg}
  POST /backup/{filename}/delete  → delete a backup file
  POST /restore/apply             → AJAX: restore from file → JSON {result, msg}
"""

import os
import pathlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

import atp_client
from session import require_session
from logging_config import logger

backup_router  = APIRouter(prefix="/backup")
restore_router = APIRouter(prefix="/restore")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"

_DATADIR = os.environ.get("ATPMGR_DATADIR", "")


# ── Backup index (GET) ────────────────────────────────────────────────────────

@backup_router.get("")
async def backup_index():
    return FileResponse(PUBLIC_DIR / "index.html")


# ── JSON: backup file list ────────────────────────────────────────────────────

@backup_router.get("/list", response_class=JSONResponse)
async def backup_list_json(
    session: dict = Depends(require_session),
):
    """Return backup files as JSON, mapping backend fields to frontend schema."""
    _, _, backup_files = await _backup_page_data()
    return [
        {
            "filename":   f["name"],
            "size_bytes": round(f["size_mb"] * 1024 * 1024),
            "created_at": f["mtime"],
        }
        for f in backup_files
    ]


# ── Create backup (AJAX POST) ─────────────────────────────────────────────────

@backup_router.post("/create", response_class=JSONResponse)
async def backup_create(
    session: dict = Depends(require_session),
):
    backup_path = await _get_backup_path()
    if not backup_path:
        return {"result": "Failed", "msg": "Backup path not configured. Set it in General Settings."}

    p = Path(backup_path)
    if not p.exists() or not p.is_dir():
        return {"result": "Failed", "msg": f"Backup directory does not exist: {backup_path}"}
    if not os.access(backup_path, os.W_OK):
        return {"result": "writeblocked", "msg": f"Backup directory is not writable: {backup_path}"}

    try:
        result = await atp_client.backup_create(backup_path)
        logger.info("Backup created: path=%r by %r", backup_path, session.get("username"))
        return {"result": "success", "msg": result or "Backup created successfully."}
    except atp_client.AtpBackendError as exc:
        logger.error("Backup create failed: %s", exc)
        return {"result": "Failed", "msg": str(exc)}


# ── Delete backup file ────────────────────────────────────────────────────────

@backup_router.post("/{filename}/delete")
async def backup_delete_file(
    filename: str,
    session: dict = Depends(require_session),
):
    backup_path = await _get_backup_path()
    if backup_path:
        safe_name = Path(filename).name
        target = Path(backup_path) / safe_name
        if target.exists() and target.parent == Path(backup_path):
            try:
                target.unlink()
                logger.info("Backup file deleted: %r by %r", safe_name, session.get("username"))
            except OSError as exc:
                logger.error("Backup file delete failed: %r: %s", safe_name, exc)
    return RedirectResponse(url="/backup", status_code=303)


# ── Restore (AJAX POST) ───────────────────────────────────────────────────────

@restore_router.post("/apply", response_class=JSONResponse)
async def restore_apply(
    request: Request,
    session: dict = Depends(require_session),
):
    body = await request.json()
    filename = (body.get("filename") or "").strip()
    if not filename:
        return {"result": "Failed", "msg": "No backup file selected."}

    backup_path = await _get_backup_path()
    if not backup_path:
        return {"result": "Failed", "msg": "Backup path not configured."}

    safe_name = Path(filename).name
    restore_path = str(Path(backup_path) / safe_name)

    try:
        result = await atp_client.restore_apply(restore_path)
        logger.info("Restore applied: file=%r by %r", safe_name, session.get("username"))
        return {"result": "success", "msg": result or "Restore completed."}
    except atp_client.AtpBackendError as exc:
        logger.error("Restore failed: file=%r: %s", safe_name, exc)
        return {"result": "Failed", "msg": str(exc)}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_backup_path() -> Optional[str]:
    try:
        settings = await atp_client.settings_get("general")
        return (settings.get("backup_restore_path", {}).get("value") or "").strip() or None
    except atp_client.AtpBackendError:
        return None


async def _backup_page_data() -> tuple[Optional[str], bool, list]:
    backup_path = await _get_backup_path()
    if not backup_path:
        return None, False, []

    p = Path(backup_path)
    path_writable = p.is_dir() and os.access(backup_path, os.W_OK)
    backup_files  = []

    if p.is_dir():
        for f in sorted(p.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if "AMPbkup" in f.name and f.name.endswith(".tar.gz"):
                try:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
                    backup_files.append({
                        "name":    f.name,
                        "mtime":   mtime.strftime("%Y-%m-%d %H:%M UTC"),
                        "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
                    })
                except OSError:
                    pass

    return backup_path, path_writable, backup_files
