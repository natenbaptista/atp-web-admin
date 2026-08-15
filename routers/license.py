"""
routers/license.py — /license/*

Ports: LicenseManagementController.php

Routes:
  GET  /license                    → license page (SPA)
  POST /license/enroll             → enroll a station
  GET  /license/stations           → list licensed stations (JSON)
  GET  /license/status             → system license status (JSON)
  POST /license/generate-passphrase → run enrollment --auto-generate
  GET  /license/download-passphrase → download /var/tmp/chaviman
  POST /license/upload-passphrase   → upload file → /var/tmp/chaviman
  POST /license/extract-key         → run enrollment --auto-extract
  POST /license/make-license        → run enrollment --auto-bulk-create
  POST /license/make-authorize      → run enrollment --authorize-make
  POST /license/apply-authorize     → run enrollment --authorize-apply
  GET  /license/check-authorize     → run enrollment --authorize-check
"""

import asyncio
import os
import pathlib
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

import atp_client
from session import require_session
from logging_config import logger

router = APIRouter(prefix="/license")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"


# ── Path helpers ───────────────────────────────────────────────────────────────

def _enrollment_binary() -> str:
    # Explicit override takes priority
    atp_apps = os.environ.get("ATP_APPS_DIR", "")
    if atp_apps:
        return str(pathlib.Path(atp_apps) / "enrollment")
    # Well-known install path
    return "/home/atp/Downloads/atp-release/atp-apps/enrollment"


def _tmp_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("LICENSE_TMP_DIR", "/var/tmp"))


def _chaviman() -> pathlib.Path:
    return _tmp_dir() / "chaviman"


def _password_file() -> pathlib.Path:
    return _tmp_dir() / "password"


def _errors_file() -> pathlib.Path:
    return _tmp_dir() / "errors"


def _superuser_file() -> pathlib.Path:
    atp_apps = os.environ.get("ATP_APPS_DIR", "")
    if atp_apps:
        return pathlib.Path(atp_apps) / "superuser"
    return pathlib.Path("/home/atp/Downloads/atp-release/atp-apps/superuser")


def _lic_stations() -> pathlib.Path:
    override = os.environ.get("LIC_STATIONS_FILE", "")
    if override:
        return pathlib.Path(override)
    return pathlib.Path("/home/atp/backup/lic_stations")


# ── Binary runner ──────────────────────────────────────────────────────────────

async def _run_enrollment(*args: str, timeout: int = 30) -> tuple[int, str]:
    """
    Run the enrollment binary with the given arguments.
    Returns (returncode, combined_stdout+stderr).
    """
    binary = _enrollment_binary()
    if not os.path.isfile(binary):
        raise HTTPException(
            status_code=503,
            detail=f"Enrollment binary not found: {binary}",
        )
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
        logger.info("enrollment %s → rc=%d", " ".join(args), proc.returncode)
        return proc.returncode, output.strip()
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Enrollment timed out after 30 seconds.")
    except Exception as exc:
        logger.error("enrollment %s error: %s", args, exc)
        raise HTTPException(status_code=500, detail=str(exc))


def _read_or_none(path: pathlib.Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


# ── Index ──────────────────────────────────────────────────────────────────────

@router.get("")
async def license_index():
    return FileResponse(PUBLIC_DIR / "index.html")


# ── Clear tmp files (called on page load) ──────────────────────────────────────

@router.get("/clear-tmp", response_class=JSONResponse)
async def clear_tmp(session: dict = Depends(require_session)):
    """Delete /var/tmp/password, /var/tmp/chaviman, /var/tmp/errors on page open."""
    for f in [_chaviman(), _password_file(), _errors_file()]:
        f.unlink(missing_ok=True)
    return {"cleared": True}


# ── Station Authorization guard ────────────────────────────────────────────────

@router.get("/has-superuser", response_class=JSONResponse)
async def has_superuser(session: dict = Depends(require_session)):
    """Return whether the station-authorization feature is available."""
    return {"has_superuser": _superuser_file().exists()}


@router.get("/station-auth-passphrase", response_class=JSONResponse)
async def station_auth_passphrase(session: dict = Depends(require_session)):
    """Return the current content of /var/tmp/chaviman (the output passphrase)."""
    content = _read_or_none(_chaviman())
    return {"passphrase": content or ""}


# ── System status ──────────────────────────────────────────────────────────────

@router.get("/status", response_class=JSONResponse)
async def license_status(session: dict = Depends(require_session)):
    """
    Read lic_stations file and return formatted status.
    File format: {numStations}-{YYYY-MM-DD}
    """
    content = _read_or_none(_lic_stations())
    if not content:
        return {"licensed": False, "status": "System is not ready for stations."}
    try:
        parts = content.split("-", 1)
        num_stations = parts[0].strip()
        date_str = parts[1].strip() if len(parts) > 1 else ""
        # File format is N-YYYYMMDD (8-digit date, no dashes); display as YYYY MM DD
        formatted = date_str
        for fmt in ("%Y%m%d", "%Y-%m-%d"):
            try:
                formatted = datetime.strptime(date_str, fmt).strftime("%Y %m %d")
                break
            except ValueError:
                continue
        return {
            "licensed": True,
            "num_stations": num_stations,
            "valid_until": formatted,
            "status": f"System is licensed for {num_stations} stations valid until {formatted}.",
        }
    except Exception:
        return {"licensed": True, "status": content}


# ── Enroll ─────────────────────────────────────────────────────────────────────

@router.post("/enroll", response_class=JSONResponse)
async def license_enroll(
    station_id: str = Form(...),
    session: dict = Depends(require_session),
):
    station_id = station_id.strip()
    if not station_id:
        raise HTTPException(status_code=400, detail="Station ID is required.")

    rc, output = await _run_enrollment("--enroll", station_id)
    success = rc == 0
    logger.info("Enroll station=%r rc=%d by=%r", station_id, rc, session.get("username"))
    return {"success": success, "message": output or ("Enrolled" if success else "Failed")}


# ── Licensed stations ──────────────────────────────────────────────────────────

@router.get("/stations", response_class=JSONResponse)
async def license_stations(session: dict = Depends(require_session)):
    return await _safe_licensed_stations()


async def _safe_licensed_stations() -> list:
    try:
        reply = await atp_client.request("controller_license_search", {})
        p = reply.get("payload", [])
        if isinstance(p, list):
            return p
        if isinstance(p, dict):
            return p.get("stations", [])
        return []
    except atp_client.AtpBackendError:
        return []


# ── Generate passphrase ────────────────────────────────────────────────────────

@router.post("/generate-passphrase", response_class=JSONResponse)
async def generate_passphrase(session: dict = Depends(require_session)):
    """
    Run enrollment --auto-generate.
    Reads the generated passphrase from /var/tmp/chaviman.
    """
    # Clean up stale tmp files before generating
    for f in [_chaviman(), _password_file(), _errors_file()]:
        f.unlink(missing_ok=True)

    await _run_enrollment("--auto-generate")

    if _chaviman().exists():
        data = _read_or_none(_chaviman()) or ""
        return {"result": "success", "data": data}
    if _errors_file().exists():
        data = _read_or_none(_errors_file()) or "Unknown error"
        _errors_file().unlink(missing_ok=True)
        return {"result": "fail", "data": data}
    return {"result": "fail", "data": "Something went wrong. Please try again."}


# ── Download passphrase ────────────────────────────────────────────────────────

@router.get("/download-passphrase")
async def download_passphrase(session: dict = Depends(require_session)):
    """Serve /var/tmp/chaviman as a timestamped file download (YYYYMMDDHHMMSS-key)."""
    path = _chaviman()
    if not path.exists():
        raise HTTPException(status_code=404, detail="No passphrase file found. Generate one first.")
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"{ts}-key"
    return FileResponse(
        path,
        filename=filename,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Upload passphrase ──────────────────────────────────────────────────────────

@router.post("/upload-passphrase", response_class=JSONResponse)
async def upload_passphrase(
    session: dict = Depends(require_session),
    passphrase_file: UploadFile = File(...),
):
    """Upload a passphrase file to /var/tmp/chaviman."""
    data = await passphrase_file.read()
    if not data.strip():
        return {"result": "fail", "data": "Invalid passphrase: file is empty."}
    try:
        _chaviman().write_bytes(data)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write passphrase file: {exc}")

    content = data.decode(errors="replace").strip()
    return {"result": "success", "data": content}


# ── Extract key ────────────────────────────────────────────────────────────────

@router.post("/extract-key", response_class=JSONResponse)
async def extract_key(
    passphrase: str = Form(""),
    session: dict = Depends(require_session),
):
    """
    Write passphrase to chaviman (if not already there), then run
    enrollment --auto-extract /var/tmp/chaviman.
    Returns the extracted key from /var/tmp/password.
    """
    passphrase = passphrase.strip()
    if not passphrase:
        return {"result": "fail", "data": "Passphrase should not be empty."}

    # Always write passphrase to chaviman (update even if file exists)
    try:
        _chaviman().write_text(passphrase, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    _errors_file().unlink(missing_ok=True)
    await _run_enrollment("--auto-extract", str(_chaviman()))

    if _password_file().exists():
        data = _read_or_none(_password_file()) or ""
        return {"result": "success", "data": data}
    if _errors_file().exists():
        data = _read_or_none(_errors_file()) or "Unknown error"
        _errors_file().unlink(missing_ok=True)
        return {"result": "fail", "data": data}
    return {"result": "fail", "data": "Something went wrong. Please try again."}


# ── Make license ───────────────────────────────────────────────────────────────

@router.post("/make-license", response_class=JSONResponse)
async def make_license(
    input_key: str = Form(...),
    stations: str = Form(...),
    validity_date: str = Form(...),
    session: dict = Depends(require_session),
):
    """
    Write key to /var/tmp/password, then run:
    enrollment --auto-bulk-create {YYYYMMDD} {stations} /var/tmp/password
    """
    if not input_key.strip() or not stations.strip() or not validity_date.strip():
        return {"result": "fail", "data": "Stations, Validity Date and Key should not be empty."}

    # Convert date to YYYYMMDD
    try:
        formatted_date = datetime.strptime(validity_date.strip(), "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        try:
            formatted_date = datetime.strptime(validity_date.strip(), "%d-%m-%Y").strftime("%Y%m%d")
        except ValueError:
            return {"result": "fail", "data": f"Invalid date format: {validity_date}"}

    try:
        _password_file().write_text(input_key.strip(), encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    await _run_enrollment("--auto-bulk-create", formatted_date, stations.strip(), str(_password_file()))

    if _errors_file().exists():
        data = _read_or_none(_errors_file()) or "Unknown error"
        _errors_file().unlink(missing_ok=True)
        return {"result": "fail", "data": data}
    return {"result": "success", "data": "License Generated Successfully!"}


# ── Make authorize ─────────────────────────────────────────────────────────────

@router.post("/make-authorize", response_class=JSONResponse)
async def make_authorize(
    input_ml_key: str = Form(...),
    session: dict = Depends(require_session),
):
    """
    Write miscellaneous config key to /var/tmp/password, then run
    enrollment --authorize-make /var/tmp/password.
    Returns the auth token from /var/tmp/chaviman.
    """
    if not input_ml_key.strip():
        return {"result": "fail", "data": "Miscellaneous config key should not be empty."}

    try:
        _password_file().write_text(input_ml_key.strip(), encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    _errors_file().unlink(missing_ok=True)
    await _run_enrollment("--authorize-make", str(_password_file()))

    if _errors_file().exists():
        data = _read_or_none(_errors_file()) or "Unknown error"
        _errors_file().unlink(missing_ok=True)
        return {"result": "fail", "data": data}
    if _chaviman().exists():
        data = _read_or_none(_chaviman()) or ""
        return {"result": "success", "data": data}
    return {"result": "fail", "data": "Something went wrong. Please try again."}


# ── Apply authorize ────────────────────────────────────────────────────────────

@router.post("/apply-authorize", response_class=JSONResponse)
async def apply_authorize(
    passphrase: str = Form(""),
    session: dict = Depends(require_session),
):
    """
    Write passphrase to chaviman (if not already there), then run
    enrollment --authorize-apply /var/tmp/chaviman.
    """
    passphrase = passphrase.strip()
    if not passphrase:
        return {"result": "fail", "data": "Passphrase should not be empty."}

    try:
        _chaviman().write_text(passphrase, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    await _run_enrollment("--authorize-apply", str(_chaviman()))

    if _errors_file().exists():
        data = _read_or_none(_errors_file()) or "Authorization failed."
        _errors_file().unlink(missing_ok=True)
        return {"result": "fail", "data": data}
    return {"result": "success", "data": "Authorization Applied"}


# ── Check authorize ────────────────────────────────────────────────────────────

@router.get("/check-authorize", response_class=JSONResponse)
async def check_authorize(session: dict = Depends(require_session)):
    """Run enrollment --authorize-check; success if no errors file appears."""
    _errors_file().unlink(missing_ok=True)

    await _run_enrollment("--authorize-check")

    if _errors_file().exists():
        data = _read_or_none(_errors_file()) or "Authorization FAILED"
        _errors_file().unlink(missing_ok=True)
        return {"result": "fail", "data": data}
    return {"result": "success", "data": "Station Authorization OK"}
