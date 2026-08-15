"""
routers/media.py — /media/*

Ports: atp-dev/src/manager/app/Controller/MediaController.php

Routes:
  GET  /media/ringtones                      → ringtone list
  POST /media/ringtones                      → upload new ringtone
  POST /media/ringtones/update               → edit ringtone description
  POST /media/ringtones/{name}/delete        → delete ringtone
  GET  /media/set-default                    → AJAX set default ringtone
  GET  /media/dialtone                       → dialtone page
  POST /media/dialtone                       → upload or clear dialtone
  GET  /ringtones/{filename}                 → serve custom ringtone WAV
  GET  /default_ringtones/{filename}         → serve system ringtone WAV
  GET  /dialtone/{filename}                  → serve dialtone WAV
"""

import os
import pathlib
import sqlite3
import struct
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

import atp_client
from session import require_session
from logging_config import logger

router = APIRouter()

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"

_DATADIR       = os.environ.get("ATPMGR_DATADIR", "")
_MEDIA_DIR     = Path(_DATADIR) / "media" if _DATADIR else Path("/tmp/media")
_RINGTONE_DIR  = _MEDIA_DIR / "ringtones"
_DIALTONE_DIR  = _MEDIA_DIR / "dialtone"

_MAX_RINGTONE_BYTES = 1 * 1024 * 1024   # 1 MB
_MAX_DIALTONE_BYTES = 10 * 1024 * 1024  # 10 MB
_MAX_CUSTOM_RINGTONES = 20

_SYSTEM_RINGTONES = {"line_ring", "office_phone", "old_school_ringtone",
                     "relax_ringtone", "lollipop"}


def _wav_bit_depth(data: bytes) -> int | None:
    """Return bits-per-sample from a WAV header, or None if not a valid WAV."""
    if len(data) < 36 or data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        return None
    pos = 12
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        csz = struct.unpack_from('<I', data, pos + 4)[0]
        if cid == b'fmt ' and csz >= 16 and pos + 22 + 2 <= len(data):
            return struct.unpack_from('<H', data, pos + 22)[0]
        pos += 8 + csz + (csz & 1)
    return None


# ── Ringtones ─────────────────────────────────────────────────────────────────

@router.get("/ringtones/names", response_class=JSONResponse)
async def ringtones_names(session: dict = Depends(require_session)):
    """Return all ringtone names (excluding the empty-string fallback) for dropdowns."""
    datadir = os.environ.get("ATPMGR_DATADIR", "")
    if not datadir:
        return []
    db_path = os.path.join(datadir, "config.sqlite")
    try:
        con = sqlite3.connect(db_path)
        rows = con.execute("SELECT name FROM ringtones WHERE name != '' ORDER BY name").fetchall()
        con.close()
        return [r[0] for r in rows]
    except Exception as exc:
        logger.warning("ringtones_names: could not query ringtones: %s", exc)
        return []


@router.get("/media/ringtones")
async def ringtones_get():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/media/ringtones")
async def ringtones_upload(
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    name        = (form.get("name") or "").strip()
    description = (form.get("description") or "").strip()
    upload      = form.get("file")

    if not name or not upload or not hasattr(upload, "filename"):
        return RedirectResponse(url="/media/ringtones", status_code=303)

    content = await upload.read()
    if len(content) > _MAX_RINGTONE_BYTES:
        logger.warning("Ringtone upload rejected (too large): %r", upload.filename)
        return RedirectResponse(url="/media/ringtones", status_code=303)

    if not upload.filename.lower().endswith(".wav"):
        logger.warning("Ringtone upload rejected (not WAV): %r", upload.filename)
        return RedirectResponse(url="/media/ringtones", status_code=303)

    bits = _wav_bit_depth(content)
    if bits is None:
        logger.warning("Ringtone upload rejected (invalid WAV): %r", upload.filename)
        return RedirectResponse(url="/media/ringtones", status_code=303)
    if bits != 16:
        logger.warning("Ringtone upload rejected (bit depth %d): %r", bits, upload.filename)
        return RedirectResponse(url="/media/ringtones", status_code=303)

    _RINGTONE_DIR.mkdir(parents=True, exist_ok=True)
    dest = _RINGTONE_DIR / f"{name}.wav"
    dest.write_bytes(content)

    ring_type = "default" if name in _SYSTEM_RINGTONES else "custom"
    try:
        await atp_client.ringtone_create({"name": name, "description": description, "type": ring_type})
        logger.info("Ringtone uploaded: %r by %r", name, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Ringtone create failed: %r: %s", name, exc)

    return RedirectResponse(url="/media/ringtones", status_code=303)


@router.post("/media/ringtones/update")
async def ringtones_update(
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    name        = (form.get("name") or "").strip()
    description = (form.get("description") or "").strip()
    if name:
        ring_type = "default" if name in _SYSTEM_RINGTONES else "custom"
        try:
            await atp_client.ringtone_update({"name": name, "description": description, "type": ring_type})
            logger.info("Ringtone updated: %r by %r", name, session.get("username"))
        except atp_client.AtpBackendError as exc:
            logger.error("Ringtone update failed: %r: %s", name, exc)
    return RedirectResponse(url="/media/ringtones", status_code=303)


@router.post("/media/ringtones/{name}/delete")
async def ringtones_delete(
    name: str,
    session: dict = Depends(require_session),
):
    # Remove file
    dest = _RINGTONE_DIR / f"{Path(name).name}.wav"
    if dest.exists():
        try:
            dest.unlink()
        except OSError:
            pass
    try:
        await atp_client.ringtone_delete(name)
        logger.info("Ringtone deleted: %r by %r", name, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Ringtone delete failed: %r: %s", name, exc)
    return JSONResponse({"result": "success"})


@router.get("/media/set-default", response_class=JSONResponse)
async def ringtones_set_default(
    ringtone_name: str,
    session: dict = Depends(require_session),
):
    if not ringtone_name:
        return {"result": "Failed", "msg": "Ringtone name is empty."}
    try:
        await atp_client.ringtone_set_default(ringtone_name)
        logger.info("Default ringtone set: %r by %r", ringtone_name, session.get("username"))
        return {"result": "success"}
    except atp_client.AtpBackendError as exc:
        logger.error("Set default ringtone failed: %r: %s", ringtone_name, exc)
        return {"result": "Failed", "msg": str(exc)}


# ── Dialtone ──────────────────────────────────────────────────────────────────

@router.get("/media/dialtone")
async def dialtone_get():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/media/dialtone")
async def dialtone_post(
    request: Request,
    session: dict = Depends(require_session),
):
    form   = await request.form()
    action = form.get("action", "upload")

    if action == "clear":
        dest = _DIALTONE_DIR / "dialtone.wav"
        if dest.exists():
            dest.unlink()
            logger.info("Dialtone cleared by %r", session.get("username"))
        try:
            await atp_client.settings_update("general", {"dial_tone": ""})
        except atp_client.AtpBackendError:
            pass
        return RedirectResponse(url="/media/dialtone", status_code=303)

    upload = form.get("upload")
    if not upload or not hasattr(upload, "filename") or not upload.filename:
        return RedirectResponse(url="/media/dialtone", status_code=303)

    content = await upload.read()
    if len(content) > _MAX_DIALTONE_BYTES:
        logger.warning("Dialtone upload rejected (too large)")
        return RedirectResponse(url="/media/dialtone", status_code=303)

    if not upload.filename.lower().endswith(".wav"):
        logger.warning("Dialtone upload rejected (not WAV): %r", upload.filename)
        return RedirectResponse(url="/media/dialtone", status_code=303)

    bits = _wav_bit_depth(content)
    if bits is None:
        logger.warning("Dialtone upload rejected (invalid WAV): %r", upload.filename)
        return RedirectResponse(url="/media/dialtone", status_code=303)
    if bits != 16:
        logger.warning("Dialtone upload rejected (bit depth %d): %r", bits, upload.filename)
        return RedirectResponse(url="/media/dialtone", status_code=303)

    _DIALTONE_DIR.mkdir(parents=True, exist_ok=True)
    dest = _DIALTONE_DIR / "dialtone.wav"
    dest.write_bytes(content)
    logger.info("Dialtone uploaded by %r", session.get("username"))

    try:
        import hashlib
        md5 = hashlib.md5(content).hexdigest()
        await atp_client.settings_update("general", {"dial_tone": md5})
    except Exception:
        pass

    return RedirectResponse(url="/media/dialtone", status_code=303)


# ── Static WAV serving ────────────────────────────────────────────────────────

@router.get("/ringtones/{filename}")
async def serve_ringtone(filename: str, session: dict = Depends(require_session)):
    path = _RINGTONE_DIR / Path(filename).name
    if path.exists():
        return FileResponse(str(path), media_type="audio/wav")
    return JSONResponse({"error": "not found"}, status_code=404)


@router.get("/dialtone/{filename}")
async def serve_dialtone(filename: str, session: dict = Depends(require_session)):
    path = _DIALTONE_DIR / Path(filename).name
    if path.exists():
        return FileResponse(str(path), media_type="audio/wav")
    return JSONResponse({"error": "not found"}, status_code=404)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _ringtone_list() -> tuple[list, str]:
    try:
        ringtones = await atp_client.ringtone_search()
        default   = next((r["name"] for r in ringtones if r.get("set_default")), "line_ring")
        return ringtones, default
    except atp_client.AtpBackendError as exc:
        logger.error("Ringtone list failed: %s", exc)
        return [], "line_ring"
