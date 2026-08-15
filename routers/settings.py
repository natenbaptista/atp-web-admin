"""
routers/settings.py — /settings/*

Ports: atp-dev/src/manager/app/Controller/SettingsController.php

Routes:
  GET  /settings/general                  → general settings form
  POST /settings/save                     → AJAX save settings → JSON
  POST /settings/reset/{name}             → AJAX reset one setting → JSON
  GET  /settings/node                     → node settings
  POST /settings/node/restart             → restart node
  GET  /settings/alarms                   → alarm settings
  GET  /settings/hold-music               → hold music management
  POST /settings/hold-music               → upload / reorder
  POST /settings/hold-music/{fn}/delete   → delete one file
  GET  /settings/logo                     → tablet logo
  POST /settings/logo                     → upload logo
  POST /settings/logo/clear               → clear logo(s)
  GET  /settings/blacklist                → blacklisted numbers
  POST /settings/blacklist                → save blacklisted numbers
  GET  /settings/whitelist                → whitelisted numbers
  POST /settings/whitelist                → save whitelisted numbers
  GET  /settings/monitoring               → monitoring nodes
  POST /settings/monitoring               → save monitoring config
  GET  /settings/intercom                 → intercom settings
"""

import hashlib
import json
import os
import pathlib
import shutil
import struct
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

import atp_client
from session import require_session
from logging_config import logger

router = APIRouter(prefix="/settings")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"

_DATADIR = os.environ.get("ATPMGR_DATADIR", "")
_LOGO_DIR      = Path(_DATADIR) / "tablet-logo"  if _DATADIR else Path("/tmp/tablet-logo")
_HOLD_MUSIC_DIR= Path(_DATADIR) / "hold_music"   if _DATADIR else Path("/tmp/hold_music")
_MONITORING_CFG= Path(_DATADIR) / "monitoring.json" if _DATADIR else Path("/tmp/monitoring.json")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


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
        pos += 8 + csz + (csz & 1)  # chunks are word-aligned
    return None


# ── General ───────────────────────────────────────────────────────────────────

@router.get("/general")
async def settings_general():
    return FileResponse(PUBLIC_DIR / "index.html")


# ── JSON: general settings ────────────────────────────────────────────────────

@router.get("/general/json", response_class=JSONResponse)
async def settings_general_json(
    session: dict = Depends(require_session),
):
    """Return general settings as JSON for the React frontend.
    React expects: {"settings": [{name, value, source}, ...]}
    """
    settings = await _safe_settings_get("general")
    # settings_get returns {name: {value, source, description}} — re-wrap for React
    return {
        "settings": [
            {"name": k, "value": v.get("value", ""), "source": v.get("source", "")}
            for k, v in settings.items()
        ]
    }


# ── AJAX save (all sections) ──────────────────────────────────────────────────

@router.post("/save", response_class=JSONResponse)
async def settings_save(
    request: Request,
    section: str = "general",
    session: dict = Depends(require_session),
):
    form = await request.form()
    data = {k: v for k, v in form.multi_items() if k not in ("csrf_token", "section")}
    try:
        await atp_client.settings_update(section, data)
        logger.info("Settings saved: section=%r by=%r", section, session.get("username"))
        return {"message": "Settings saved successfully."}
    except atp_client.AtpBackendError as exc:
        logger.error("Settings save failed: section=%r: %s", section, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)


# ── AJAX reset one setting ────────────────────────────────────────────────────

@router.post("/reset/{name}", response_class=JSONResponse)
async def settings_reset(
    name: str,
    node_dn: Optional[str] = None,
    session: dict = Depends(require_session),
):
    try:
        result = await atp_client.settings_reset(name, node_dn=node_dn)
        logger.info("Setting reset: %r node_dn=%r by=%r", name, node_dn, session.get("username"))
        return result  # {name: {value, source}}
    except atp_client.AtpBackendError as exc:
        logger.error("Setting reset failed: %r: %s", name, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)


# ── Node settings ─────────────────────────────────────────────────────────────

@router.get("/node")
async def settings_node():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.get("/nodes/json", response_class=JSONResponse)
async def settings_nodes_json(session: dict = Depends(require_session)):
    """Return connected nodes for the Node Restart page.
    Uses controller_request_connections; each item is a ConnectionInfo dict with:
      remote_dn: {"CN": str, "role": str, "O": str}
      remote_endpoint: {"host": str, "port": int}
    """
    try:
        connections = await atp_client.node_connections()
    except atp_client.AtpBackendError:
        connections = []

    result = []
    for conn in connections:
        if not isinstance(conn, dict):
            continue
        remote_dn = conn.get("remote_dn") or {}
        remote_ep = conn.get("remote_endpoint") or {}
        cn        = remote_dn.get("CN", "")
        node_type = remote_dn.get("role", "")
        host      = remote_ep.get("host", "")
        if cn:
            result.append({"cn": cn, "host": host, "node_type": node_type})
    return result


@router.post("/node/restart", response_class=JSONResponse)
async def settings_node_restart(
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    target = form.get("target", "")
    try:
        await atp_client.node_restart(target)
        logger.info("Node restart: target=%r by=%r", target, session.get("username"))
        return {"message": f"Restart signal sent to {target}."}
    except atp_client.AtpBackendError as exc:
        logger.error("Node restart failed: target=%r: %s", target, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)


# ── Alarm settings ────────────────────────────────────────────────────────────

@router.get("/alarms")
async def settings_alarms():
    return FileResponse(PUBLIC_DIR / "index.html")


# ── Hold music ────────────────────────────────────────────────────────────────

@router.get("/hold-music")
async def settings_hold_music():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.get("/hold-music/json", response_class=JSONResponse)
async def settings_hold_music_json(session: dict = Depends(require_session)):
    """Return hold music files as JSON."""
    return {"files": _list_hold_music()}


@router.post("/hold-music")
async def settings_hold_music_post(
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    action = form.get("action", "reorder")

    if action == "upload":
        upload = form.get("upload")
        if upload and hasattr(upload, "filename") and upload.filename:
            fname = Path(upload.filename).name
            if not fname.lower().endswith(".wav"):
                logger.warning("Hold music upload rejected (not WAV): %r", fname)
                return JSONResponse({"result": "fail", "errors": [f"{fname}: Only .wav files may be uploaded."]}, status_code=400)
            content = await upload.read()
            if len(content) > MAX_UPLOAD_BYTES:
                logger.warning("Hold music upload rejected (too large): %r", fname)
                return JSONResponse({"result": "fail", "errors": [f"{fname}: File exceeds 10 MB limit."]}, status_code=400)
            bits = _wav_bit_depth(content)
            if bits is None:
                logger.warning("Hold music upload rejected (invalid WAV): %r", fname)
                return JSONResponse({"result": "fail", "errors": [f"{fname}: Not a valid WAV file."]}, status_code=400)
            if bits != 16:
                logger.warning("Hold music upload rejected (bit depth %d): %r", bits, fname)
                return JSONResponse({"result": "fail", "errors": [f"{fname}: Invalid bit depth; got {bits}-bit, requires 16-bit."]}, status_code=400)
            _HOLD_MUSIC_DIR.mkdir(parents=True, exist_ok=True)
            dest = _HOLD_MUSIC_DIR / fname
            dest.write_bytes(content)
            logger.info("Hold music uploaded: %r by %r", fname, session.get("username"))
            return JSONResponse({"result": "success"})

    elif action == "reorder":
        order = form.getlist("order[]")
        enabled = {k.split("[")[1].rstrip("]"): v
                   for k, v in form.multi_items() if k.startswith("enabled[")}
        # Persist order + enabled state via atp_client or file
        try:
            await atp_client.hold_music_update(order, enabled)
        except atp_client.AtpBackendError as exc:
            logger.error("Hold music reorder failed: %s", exc)

    return RedirectResponse(url="/settings/hold-music", status_code=303)


@router.post("/hold-music/{filename}/delete")
async def settings_hold_music_delete(
    filename: str,
    session: dict = Depends(require_session),
):
    dest = _HOLD_MUSIC_DIR / Path(filename).name  # prevent path traversal
    if dest.exists() and dest.parent == _HOLD_MUSIC_DIR:
        dest.unlink()
        logger.info("Hold music deleted: %r by %r", filename, session.get("username"))
    try:
        await atp_client.hold_music_delete(filename)
    except atp_client.AtpBackendError:
        pass
    return RedirectResponse(url="/settings/hold-music", status_code=303)


# ── Tablet logo ───────────────────────────────────────────────────────────────

@router.get("/logo")
async def settings_logo():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.get("/logo/json", response_class=JSONResponse)
async def settings_logo_json(session: dict = Depends(require_session)):
    """Return logo status as JSON — URLs match /settings/logo/image/{side}."""
    left, right = _current_logos()
    return {
        "left":  "/settings/logo/image/left"  if left  else None,
        "right": "/settings/logo/image/right" if right else None,
    }


@router.get("/logo/image/{side}")
async def settings_logo_image(side: str, session: dict = Depends(require_session)):
    """Serve the current logo PNG file for preview."""
    if side not in ("left", "right"):
        return JSONResponse({"error": "Invalid side"}, status_code=400)
    p = _LOGO_DIR / f"customer-logo-{side}.png"
    if not p.exists():
        return JSONResponse({"error": "No logo"}, status_code=404)
    from fastapi.responses import Response
    return Response(content=p.read_bytes(), media_type="image/png")


@router.post("/logo")
async def settings_logo_upload(
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    upload = form.get("upload")
    side   = form.get("side", "left")
    if side not in ("left", "right"):
        side = "left"

    if upload and hasattr(upload, "filename") and upload.filename:
        fname = Path(upload.filename).name
        if not fname.lower().endswith(".png"):
            logger.warning("Logo upload rejected (not PNG): %r", fname)
        else:
            _LOGO_DIR.mkdir(parents=True, exist_ok=True)
            dest = _LOGO_DIR / f"customer-logo-{side}.png"
            content = await upload.read()
            if len(content) <= MAX_UPLOAD_BYTES:
                dest.write_bytes(content)
                md5 = hashlib.md5(content).hexdigest()
                # Notify ATP backend — same as PHP: set_global('tablet_logo_{side}', md5)
                try:
                    await atp_client.settings_update("general", {f"tablet_logo_{side}": md5})
                except atp_client.AtpBackendError as exc:
                    logger.error("Logo settings_update failed: %s", exc)
                logger.info("Logo uploaded: side=%r md5=%s by=%r", side, md5, session.get("username"))

    return RedirectResponse(url="/settings/logo", status_code=303)


@router.post("/logo/clear")
async def settings_logo_clear(
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    side = form.get("side", "all")

    sides_to_clear = []
    if side in ("left", "all"):
        sides_to_clear.append("left")
    if side in ("right", "all"):
        sides_to_clear.append("right")

    for s in sides_to_clear:
        p = _LOGO_DIR / f"customer-logo-{s}.png"
        if p.exists():
            p.unlink()
            logger.info("Logo cleared: side=%r by=%r", s, session.get("username"))
        try:
            await atp_client.settings_update("general", {f"tablet_logo_{s}": ""})
        except atp_client.AtpBackendError as exc:
            logger.error("Logo clear settings_update failed: side=%r %s", s, exc)

    return RedirectResponse(url="/settings/logo", status_code=303)


# ── Blacklist / Whitelist ─────────────────────────────────────────────────────

@router.get("/blacklist")
async def settings_blacklist():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/blacklist")
async def settings_blacklist_post(
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    raw = form.get("numbers", "")
    numbers = [n.strip() for n in raw.split(",") if n.strip()]
    try:
        await atp_client.settings_update("blacklist", {"numbers": ",".join(numbers)})
        logger.info("Blacklist updated: %d numbers by %r", len(numbers), session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Blacklist save failed: %s", exc)
    return RedirectResponse(url="/settings/blacklist", status_code=303)


@router.get("/whitelist")
async def settings_whitelist():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/whitelist")
async def settings_whitelist_post(
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    raw = form.get("numbers", "")
    numbers = [n.strip() for n in raw.split(",") if n.strip()]
    try:
        await atp_client.settings_update("whitelist", {"numbers": ",".join(numbers)})
        logger.info("Whitelist updated: %d numbers by %r", len(numbers), session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Whitelist save failed: %s", exc)
    return RedirectResponse(url="/settings/whitelist", status_code=303)


# ── Monitoring ────────────────────────────────────────────────────────────────

@router.get("/monitoring")
async def settings_monitoring():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/monitoring", response_class=JSONResponse)
async def settings_monitoring_post(
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    raw = form.get("nodes", "[]")
    try:
        nodes = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        nodes = []

    _save_monitoring_config(nodes)
    logger.info("Monitoring config saved: %d nodes by %r", len(nodes), session.get("username"))
    return {"message": f"Monitoring configuration saved ({len(nodes)} nodes)."}


# ── Intercom ──────────────────────────────────────────────────────────────────

@router.get("/intercom")
async def settings_intercom():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.get("/dns/{site}/{keyword}", response_class=JSONResponse)
async def settings_node_dns(
    site: str,
    keyword: str,
    session: dict = Depends(require_session),
):
    """
    Return actors in a site whose CN contains keyword.
    Ports: get_node_dns() in SettingsController.php.
    Used by the node settings UI for DN autocomplete.
    """
    try:
        actors = await atp_client.site_actors(site)
        dns = {
            a.get("cn", ""): a.get("node_type", "")
            for a in actors
            if a.get("node_type", "") != "" and keyword.lower() in a.get("cn", "").lower()
        }
        return dns
    except atp_client.AtpBackendError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@router.get("/backup-path-check", response_class=JSONResponse)
async def settings_backup_path_check(
    value: str = "",
    session: dict = Depends(require_session),
):
    """
    Check whether the given path exists on the server.
    Ports: check_backup_restore_path() in SettingsController.php.
    Query param: ?value=/path/to/check
    """
    if not value:
        return {"exists": False, "path": value}
    exists = os.path.exists(value)
    return {"exists": exists, "path": value}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _safe_settings_get(section: str, node_dn: Optional[str] = None) -> dict:
    try:
        return await atp_client.settings_get(section, node_dn=node_dn)
    except atp_client.AtpBackendError:
        return {}


async def _safe_sites() -> list:
    try:
        return await atp_client.site_search()
    except atp_client.AtpBackendError:
        return []


async def _safe_nodes(site: Optional[str] = None) -> list:
    try:
        return await atp_client.node_search(site or "")
    except atp_client.AtpBackendError:
        return []


async def _safe_number_list(list_type: str) -> list:
    try:
        settings = await atp_client.settings_get(list_type)
        raw = settings.get("numbers", {}).get("value", "")
        return [n.strip() for n in raw.split(",") if n.strip()]
    except atp_client.AtpBackendError:
        return []


def _list_hold_music() -> list:
    if not _HOLD_MUSIC_DIR.exists():
        return []
    result = []
    for p in sorted(_HOLD_MUSIC_DIR.iterdir()):
        if p.suffix.lower() == ".wav":
            result.append({
                "filename": p.name,
                "enabled":  True,  # enabled state stored in backend
                "size_kb":  round(p.stat().st_size / 1024),
            })
    return result


def _current_logos() -> tuple[Optional[str], Optional[str]]:
    left  = "customer-logo-left.png"  if (_LOGO_DIR / "customer-logo-left.png").exists()  else None
    right = "customer-logo-right.png" if (_LOGO_DIR / "customer-logo-right.png").exists() else None
    return left, right


def _load_monitoring_config() -> list:
    if not _MONITORING_CFG.exists():
        return []
    try:
        return json.loads(_MONITORING_CFG.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_monitoring_config(nodes: list) -> None:
    _MONITORING_CFG.parent.mkdir(parents=True, exist_ok=True)
    _MONITORING_CFG.write_text(json.dumps(nodes, indent=2))
