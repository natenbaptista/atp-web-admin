"""
routers/stations.py — /stations/*

Ports: StationInstallerController.php

Binary: amp_station_installer
  Flags (from binary --help strings):
    --get-stations       discover connected stations → writes /var/tmp/stationslist
    --test-station <ip>  ping/test a station
    --reset-station <ip> reset station version
    --install-stations   install to IPs listed in /var/tmp/stationslist

stationslist format: one line per station, comma-separated: ip,name,user

Routes:
  GET  /stations                → SPA shell
  POST /stations/discover       → run --get-stations, parse stationslist (JSON)
  POST /stations/{id}/test      → --test-station <ip> (JSON)
  POST /stations/{id}/reset     → --reset-station <ip> (JSON)
  POST /stations/install        → --install-stations with selected IPs (SSE)
"""

import asyncio
import json
import os
import pathlib
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from session import require_session
from logging_config import logger

router = APIRouter(prefix="/stations")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"

_STATIONSLIST = Path("/var/tmp/stationslist")
_ERRORS_FILE  = Path("/var/tmp/errors")


def _installer_binary() -> str:
    """Resolve amp_station_installer path — tries all candidates, returns first that exists."""
    candidates = []

    atp_apps = os.environ.get("ATP_APPS_DIR", "")
    if atp_apps:
        candidates.append(str(Path(atp_apps) / "bin" / "amp_station_installer"))
        candidates.append(str(Path(atp_apps) / "amp_station_installer"))

    datadir = os.environ.get("ATPMGR_DATADIR", "")
    if datadir:
        candidates.append(str(Path(datadir).parent / "atp-apps" / "bin" / "amp_station_installer"))
        candidates.append(str(Path(datadir).parent / "atp-apps" / "amp_station_installer"))

    candidates += [
        "/home/atp/Downloads/atp-release/atp-apps/amp_station_installer",
        "/home/atp/atp-apps/amp_station_installer",
        "/opt/atp-apps/bin/amp_station_installer",
        "/usr/local/bin/amp_station_installer",
    ]

    for p in candidates:
        if os.path.isfile(p):
            return p

    return candidates[0] if candidates else "/opt/atp-apps/bin/amp_station_installer"


async def _run_installer(args: list, timeout: int = 60) -> tuple[bool, str]:
    binary = _installer_binary()
    if not os.path.isfile(binary):
        return False, f"Installer binary not found: {binary}"
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
        return proc.returncode == 0, output.strip()
    except asyncio.TimeoutError:
        return False, f"Command timed out after {timeout}s"
    except Exception as exc:
        return False, str(exc)


def _parse_stationslist() -> list[dict]:
    """Read /var/tmp/stationslist and parse 'ip,name,user' lines."""
    if not _STATIONSLIST.exists():
        return []
    stations = []
    for idx, raw in enumerate(_STATIONSLIST.read_text(errors="replace").splitlines()):
        line = raw.strip().rstrip(",")
        if not line:
            continue
        parts = line.split(",")
        stations.append({
            "id":      parts[0] if len(parts) > 0 else str(idx),
            "station": parts[0] if len(parts) > 0 else "",
            "name":    parts[1] if len(parts) > 1 else "",
            "user":    parts[2] if len(parts) > 2 and parts[2] else "No User",
        })
    return stations


# ── Index ─────────────────────────────────────────────────────────────────────

@router.get("")
async def stations_index():
    return FileResponse(PUBLIC_DIR / "index.html")


# ── Discover ──────────────────────────────────────────────────────────────────

@router.post("/discover", response_class=JSONResponse)
async def stations_discover(
    request: Request,
    session: dict = Depends(require_session),
):
    binary = _installer_binary()
    if not os.path.isfile(binary):
        # Binary not installed — return empty list silently (PHP shows "No connected stations")
        logger.warning("Station discover: binary not found at %r", binary)
        return JSONResponse({"success": True, "stations": [], "output": ""})

    # Clean up previous result files
    for f in (_STATIONSLIST, _ERRORS_FILE):
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass

    success, output = await _run_installer(["--get-stations"], timeout=30)
    logger.info("Station discover rc=%s by=%r output=%r", success, session.get("username"), output[:200])

    if _ERRORS_FILE.exists():
        err_msg = _ERRORS_FILE.read_text(errors="replace").strip()
        _ERRORS_FILE.unlink(missing_ok=True)
        return JSONResponse({"success": False, "stations": [], "output": err_msg or output})

    stations = _parse_stationslist()
    return JSONResponse({"success": success or bool(stations), "stations": stations, "output": output})


# ── Test ──────────────────────────────────────────────────────────────────────

@router.post("/{station_id}/test", response_class=JSONResponse)
async def stations_test(
    station_id: str,
    request: Request,
    session: dict = Depends(require_session),
):
    if not os.path.isfile(_installer_binary()):
        return JSONResponse({"success": False, "output": "Station installer not available on this system."})
    _ERRORS_FILE.unlink(missing_ok=True)
    success, output = await _run_installer(["--test-station", station_id], timeout=30)
    if _ERRORS_FILE.exists():
        err = _ERRORS_FILE.read_text(errors="replace").strip()
        _ERRORS_FILE.unlink(missing_ok=True)
        return JSONResponse({"success": False, "output": err or output})
    logger.info("Station test id=%r rc=%s by=%r", station_id, success, session.get("username"))
    return JSONResponse({"success": success, "output": output or f"Station {station_id} is OK"})


# ── Reset ─────────────────────────────────────────────────────────────────────

@router.post("/{station_id}/reset", response_class=JSONResponse)
async def stations_reset(
    station_id: str,
    request: Request,
    session: dict = Depends(require_session),
):
    if not os.path.isfile(_installer_binary()):
        return JSONResponse({"success": False, "output": "Station installer not available on this system."})
    _ERRORS_FILE.unlink(missing_ok=True)
    success, output = await _run_installer(["--reset-station", station_id], timeout=60)
    if _ERRORS_FILE.exists():
        err = _ERRORS_FILE.read_text(errors="replace").strip()
        _ERRORS_FILE.unlink(missing_ok=True)
        return JSONResponse({"success": False, "output": err or output})
    logger.info("Station reset id=%r rc=%s by=%r", station_id, success, session.get("username"))
    return JSONResponse({"success": success, "output": output})


# ── Install (SSE streaming) ───────────────────────────────────────────────────

_QUICKINSTALL = "/home/atp/Downloads/atp-release/QuickInstall.sh"


@router.post("/install")
async def stations_install(
    request: Request,
    session: dict = Depends(require_session),
):
    """Stream installation progress as Server-Sent Events.

    Flow:
      1. Write selected IPs to /var/tmp/stationslist
      2. Run --install-stations (wait, non-streaming)
      3. Check /var/tmp/errors — yield collected errors, delete file
      4. Run QuickInstall.sh and stream its output line by line
    """
    form = await request.form()
    station_ids = form.getlist("station_ids")

    async def event_stream():
        if not station_ids:
            yield f'data: {json.dumps({"error": "No stations selected"})}\n\n'
            return

        binary = _installer_binary()
        if not os.path.isfile(binary):
            yield f'data: {json.dumps({"error": f"Installer not found: {binary}"})}\n\n'
            return

        # Step 1: write selected IPs to stationslist (one IP per line)
        try:
            _STATIONSLIST.write_text("\n".join(station_ids) + "\n")
        except OSError as exc:
            yield f'data: {json.dumps({"error": str(exc)})}\n\n'
            return

        # Step 2: run --install-stations and stream its output line by line
        _ERRORS_FILE.unlink(missing_ok=True)
        install_rc = 1
        try:
            proc = await asyncio.create_subprocess_exec(
                binary, "--install-stations",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                async for line in proc.stdout:
                    text = line.decode(errors="replace").rstrip()
                    if text:
                        yield f"data: {json.dumps({'line': text})}\n\n"
                await proc.wait()
                install_rc = proc.returncode
                logger.info("Station install --install-stations rc=%d by=%r",
                            install_rc, session.get("username"))
            except asyncio.TimeoutError:
                proc.kill()
                yield f'data: {json.dumps({"error": "--install-stations timed out"})}\n\n'
                yield f'data: {json.dumps({"done": True, "rc": 1})}\n\n'
                return
        except Exception as exc:
            yield f'data: {json.dumps({"error": str(exc)})}\n\n'
            yield f'data: {json.dumps({"done": True, "rc": 1})}\n\n'
            return

        # Step 3: collect and delete /var/tmp/errors if present
        if _ERRORS_FILE.exists():
            err_text = _ERRORS_FILE.read_text(errors="replace").strip()
            _ERRORS_FILE.unlink(missing_ok=True)
            if err_text:
                yield f'data: {json.dumps({"error": err_text})}\n\n'

        # Step 4: run QuickInstall.sh and stream its output (if present)
        if not os.path.isfile(_QUICKINSTALL):
            # QuickInstall.sh is optional — signal done using --install-stations rc
            yield f'data: {json.dumps({"done": True, "rc": install_rc})}\n\n'
            return

        try:
            qs_proc = await asyncio.create_subprocess_exec(
                "bash", _QUICKINSTALL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            # Read with a per-line timeout: QuickInstall.sh may spawn background
            # children that keep the stdout pipe open after the script itself exits.
            # After 3 seconds of silence we treat it as done rather than hanging.
            while True:
                try:
                    line_bytes = await asyncio.wait_for(
                        qs_proc.stdout.readline(), timeout=3.0
                    )
                except asyncio.TimeoutError:
                    break
                if not line_bytes:
                    break
                text = line_bytes.decode(errors="replace").rstrip()
                if text:
                    yield f"data: {json.dumps({'line': text})}\n\n"

            # Ensure process is cleaned up even if children kept pipe open
            try:
                qs_proc.kill()
            except Exception:
                pass
            try:
                await asyncio.wait_for(qs_proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

            rc = qs_proc.returncode if qs_proc.returncode is not None else install_rc
            yield f"data: {json.dumps({'done': True, 'rc': rc})}\n\n"
            logger.info("Station install QuickInstall.sh rc=%s stations=%r by=%r",
                        rc, station_ids, session.get("username"))
        except Exception as exc:
            yield f'data: {json.dumps({"error": str(exc)})}\n\n'
            yield f'data: {json.dumps({"done": True, "rc": 1})}\n\n'

    return StreamingResponse(event_stream(), media_type="text/event-stream")
