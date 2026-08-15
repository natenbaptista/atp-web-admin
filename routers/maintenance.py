"""
routers/maintenance.py — /maintenance/*

Routes:
  POST /maintenance/logs/download → run amp_tool_app --node-log or --station-log,
                                     then stream zip file(s) as download.
"""

import asyncio
import io
import os
import time
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form
from fastapi.responses import JSONResponse, Response

from session import require_session
from logging_config import logger

router = APIRouter(prefix="/maintenance")

_ATP_APPS_DIR  = Path("/home/atp/Downloads/atp-release/atp-apps")
_AMP_TOOL_APP  = _ATP_APPS_DIR / "amp_tool_app"
_ERRORS_FILE   = Path("/var/tmp/errors")


async def _run_tool(args: list, timeout: int = 120) -> tuple[bool, str]:
    binary = str(_AMP_TOOL_APP)
    if not os.path.isfile(binary):
        return False, f"Tool binary not found: {binary}"
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_ATP_APPS_DIR),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = (stdout.decode(errors="replace") + stderr.decode(errors="replace")).strip()
        logger.info("_run_tool: binary=%s args=%s rc=%d cwd=%s output=%r",
                    binary, args, proc.returncode, _ATP_APPS_DIR, output[:500])
        return proc.returncode == 0, output
    except asyncio.TimeoutError:
        return False, f"Command timed out after {timeout}s"
    except Exception as exc:
        return False, str(exc)


def _error_json(msg: str) -> JSONResponse:
    return JSONResponse({"result": "fail", "data": msg})


@router.post("/logs/download")
async def download_logs(
    ip: str = Form(...),
    log_type: str = Form(...),   # "node" or "station"
    session: dict = Depends(require_session),
):
    """
    Run amp_tool_app --node-log <ip> or --station-log <ip>.
    On success, stream the generated zip file(s) as a download.
    On error (/var/tmp/errors), return JSON with the error text.
    """
    ip = ip.strip()
    if not ip:
        return _error_json("IP address is required.")

    if log_type == "node":
        args = ["--node-log", ip]
    elif log_type == "station":
        args = ["--station-log", ip]
    else:
        return _error_json("Invalid log type.")

    # Record time so we can identify newly created zip files
    start_time = time.time() - 1   # 1-second buffer for mtime precision

    _ERRORS_FILE.unlink(missing_ok=True)

    ok, output = await _run_tool(args)
    logger.info("amp_tool_app %s rc=%s ip=%r by=%r", args, ok, ip, session.get("username"))

    # Parse JSON output from amp_tool_app: {"result":"fail","data":"..."} or {"result":"ok",...}
    import json as _json
    try:
        parsed = _json.loads(output)
        if isinstance(parsed, dict) and parsed.get("result") == "fail":
            return _error_json(parsed.get("data", output))
    except (_json.JSONDecodeError, ValueError):
        pass

    if _ERRORS_FILE.exists():
        err = _ERRORS_FILE.read_text(errors="replace").strip()
        _ERRORS_FILE.unlink(missing_ok=True)
        return _error_json(err or output or "Command failed.")

    if not ok:
        return _error_json(output or "Command failed.")

    # Find zip files created/modified after the command started
    try:
        zip_files = [
            f for f in _ATP_APPS_DIR.glob("*.zip")
            if f.stat().st_mtime >= start_time
        ]
    except OSError as exc:
        return _error_json(f"Could not read output directory: {exc}")

    if not zip_files:
        return _error_json("Command ran but no log files were generated.")

    ts = datetime.now().strftime("%Y%m%d%H%M%S")

    if len(zip_files) == 1:
        content = zip_files[0].read_bytes()
        filename = zip_files[0].name
    else:
        # Bundle multiple zip files into one archive
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            for f in sorted(zip_files, key=lambda x: x.name):
                zf.write(f, f.name)
        content = buf.getvalue()
        filename = f"logs-{ts}.zip"

    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
