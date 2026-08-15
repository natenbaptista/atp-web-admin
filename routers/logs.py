"""
routers/logs.py — /logs/*

Ports: LogsController.php

Routes:
  GET  /logs          → logs page (SPA)
  POST /logs/fetch    → collect logs from a node/station IP, return download token
  GET  /logs/download/{token} → stream the generated ZIP

amp_tool_app usage (matches ubuntu14 PHP exactly):
  Node logs    : amp_tool_app --node-log    {ip}
  Station logs : amp_tool_app --station-log {ip}

After the command, the tool either:
  - Writes /var/tmp/errors with a message (e.g. "IP x.x.x.x is not reachable!")
  - Writes one or more *.zip files into the same directory as the binary
"""

import asyncio
import glob as _glob
import os
import pathlib
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse

from session import require_session
from logging_config import logger

router = APIRouter(prefix="/logs")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"

ERRORS_FILE = "/var/tmp/errors"


def _tool_binary() -> Path:
    # Explicit override wins
    explicit = os.environ.get("AMP_TOOL_APP", "")
    if explicit:
        return Path(explicit)
    # ATP_APPS_DIR points to the atp-apps directory (contains the binary directly)
    atp_apps = os.environ.get("ATP_APPS_DIR", "")
    if atp_apps:
        return Path(atp_apps) / "amp_tool_app"
    # Default: same path the PHP LogsController uses
    return Path("/home/atp/Downloads/atp-release/atp-apps/amp_tool_app")


def _zip_dir() -> Path:
    """Directory where amp_tool_app writes its ZIP output (same dir as the binary)."""
    return _tool_binary().parent


def _tmp_dir() -> Path:
    datadir = os.environ.get("ATPMGR_DATADIR", "")
    base = Path(datadir) / "tmp" if datadir else Path("/tmp/enepath")
    base.mkdir(parents=True, exist_ok=True)
    return base


# ── Index ─────────────────────────────────────────────────────────────────────

@router.get("")
async def logs_index():
    return FileResponse(PUBLIC_DIR / "index.html")


# ── Fetch logs ────────────────────────────────────────────────────────────────

@router.post("/fetch", response_class=JSONResponse)
async def logs_fetch(
    request: Request,
    session: dict = Depends(require_session),
):
    """
    Collect logs from a remote node or station by IP address.
    Body form fields: ip_address, log_type (config | station)
    """
    form       = await request.form()
    ip_address = (form.get("ip_address") or "").strip()
    log_type   = (form.get("log_type") or "station").strip()

    if not ip_address:
        return JSONResponse({"success": False, "message": "IP address is required."}, status_code=400)

    binary = _tool_binary()
    logger.info("Logs fetch: binary=%s exists=%s", binary, binary.is_file())
    if not binary.is_file():
        return JSONResponse(
            {"success": False, "message": f"amp_tool_app not found at {binary}"},
            status_code=503,
        )

    # Remove stale errors file before running
    try:
        Path(ERRORS_FILE).unlink(missing_ok=True)
    except OSError:
        pass

    flag = "--node-log" if log_type == "config" else "--station-log"
    cmd  = [str(binary), flag, ip_address]
    logger.info("Logs fetch: cmd=%s cwd=%s", " ".join(cmd), _zip_dir())

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_zip_dir()),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        raw_stdout = stdout.decode(errors="replace").strip()
        raw_stderr = stderr.decode(errors="replace").strip()
        output = (raw_stdout + ("\n" + raw_stderr if raw_stderr else "")).strip()
        logger.info("Logs fetch: ip=%r type=%r rc=%d stdout=%r stderr=%r by=%r",
                    ip_address, log_type, proc.returncode,
                    raw_stdout[:500], raw_stderr[:500], session.get("username"))
    except asyncio.TimeoutError:
        return JSONResponse({"success": False, "message": "Log collection timed out (120 s)."}, status_code=504)
    except Exception as exc:
        logger.error("Logs fetch error: ip=%r: %s", ip_address, exc)
        return JSONResponse({"success": False, "message": str(exc)}, status_code=500)

    # amp_tool_app outputs JSON: {"result":"fail","data":"..."} or {"result":"ok",...}
    import json as _json
    tool_msg = output
    try:
        parsed = _json.loads(output)
        if isinstance(parsed, dict):
            if parsed.get("result") == "fail":
                return JSONResponse({"success": False, "message": parsed.get("data", output)})
            tool_msg = parsed.get("data", output)
    except (_json.JSONDecodeError, ValueError):
        pass  # not JSON — use raw output

    # Check error file written by amp_tool_app
    errors_path = Path(ERRORS_FILE)
    if errors_path.exists():
        try:
            error_msg = errors_path.read_text().strip()
            errors_path.unlink(missing_ok=True)
        except OSError:
            error_msg = output or "Unknown error"
        return JSONResponse({"success": False, "message": error_msg or output})

    if proc.returncode != 0:
        return JSONResponse({"success": False, "message": tool_msg or f"Command failed (rc={proc.returncode})"})

    # Find generated ZIP(s) in the tool directory
    zips = sorted(_zip_dir().glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not zips:
        return JSONResponse({"success": False, "message": "No ZIP file was produced."})

    if log_type == "station":
        # Station: serve the most recently modified ZIP (turret-aXXXX.zip)
        source_zip = zips[0]
    else:
        # Node: combine all ZIPs into one named config-mixer-gateway-vr.zip
        import zipfile as _zf
        combined_name = "config-mixer-gateway-vr.zip"
        combined_path = _zip_dir() / combined_name
        if combined_path.exists():
            combined_path.unlink()
        with _zf.ZipFile(combined_path, "w", _zf.ZIP_DEFLATED) as zout:
            for z in zips:
                zout.write(z, z.name)
        source_zip = combined_path

    # Copy to a token-named file in tmp so the download route can serve it
    token = uuid.uuid4().hex
    dest  = _tmp_dir() / f"{token}.zip"
    import shutil
    shutil.copy2(source_zip, dest)

    return JSONResponse({
        "success": True,
        "message": f"Logs collected from {ip_address}.",
        "download_token": token,
        "filename": source_zip.name,
    })


# ── Download ──────────────────────────────────────────────────────────────────

@router.get("/download/{token}")
async def logs_download(
    token: str,
    session: dict = Depends(require_session),
):
    if not all(c in "0123456789abcdef" for c in token) or len(token) != 32:
        return JSONResponse({"error": "Invalid token"}, status_code=400)

    zip_path = _tmp_dir() / f"{token}.zip"
    if not zip_path.exists():
        return JSONResponse({"error": "File not found or expired."}, status_code=404)

    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_path.stem}.zip"'},
    )
