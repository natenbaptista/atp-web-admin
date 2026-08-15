"""
routers/recording.py — /recordings/*

Ports: atp-dev/src/recording/web-server/src/Template/Recordings/
       atp-dev/src/recording/web-server/src/Controller/RecordingsController.php

Routes:
  GET  /recordings                          → filter form + paginated results
  GET  /recordings/play/{rec_id}            → AJAX: JSON {url} for wavesurfer
  POST /recordings/download                 → download selected recordings as ZIP
  GET  /recordings/download-all             → download all (current filter) as ZIP
  GET  /recordings/line-permissions         → auditor ↔ lines assignment list
  GET  /recordings/line-permissions/{user}/edit   → edit auditor's allowed lines
  POST /recordings/line-permissions/{user}/edit   → save allowed lines
  GET  /recordings/servers                  → list Veriant (VR) servers
  POST /recordings/servers/add              → add Veriant server
  GET  /recordings/servers/{id}/edit        → get Veriant server by id
  POST /recordings/servers/{id}/edit        → update Veriant server
  POST /recordings/servers/{id}/delete      → delete Veriant server (runs script)
  POST /recordings/servers/delete-all       → regenerate all Veriant cfg + run update script
  GET  /recordings/periods/json             → get recording periods (UTC→local conversion)
  POST /recordings/periods                  → save recording periods (local→UTC conversion)
"""

import asyncio
import datetime
import io
import json
import os
import pathlib
import sqlite3
import zipfile
from datetime import datetime as _dt
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse

import atp_client
from session import require_session
from logging_config import logger

router = APIRouter(prefix="/recordings")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"

_PER_PAGE = 25

_DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _fmt_hm(d: _dt) -> str:
    return f"{d.hour:02d}:{d.minute:02d}"


def _periods_local_to_utc_json(periods_by_day: dict, tz_name: str) -> str:
    """
    Convert {day_name: [{start, end}]} from local timezone to a stored UTC JSON array.
    Mirrors PHP RecordingsController::recording_periods_to_utc().
    """
    try:
        local_tz = ZoneInfo(tz_name)
    except Exception:
        local_tz = ZoneInfo("UTC")
    utc_tz = ZoneInfo("UTC")

    result: list[list[str]] = [[] for _ in range(7)]

    for day_idx, day_name in enumerate(_DAYS):
        for period in periods_by_day.get(day_name, []):
            s = str(period.get("start", "")).strip()
            e = str(period.get("end",   "")).strip()
            if not s or not e:
                continue
            try:
                sh, sm = map(int, s.split(":")) if ":" in s else (int(s), 0)
                # "24:00" end = next-day 00:00 local
                if e in ("24:00", "24"):
                    dt2 = _dt(2016, 2, 21, 0, 0, tzinfo=local_tz)
                else:
                    eh, em = map(int, e.split(":")) if ":" in e else (int(e), 0)
                    dt2 = _dt(2016, 2, 20, eh, em, tzinfo=local_tz)
                dt1 = _dt(2016, 2, 20, sh, sm, tzinfo=local_tz)
            except (ValueError, KeyError):
                continue

            u1 = dt1.astimezone(utc_tz)
            u2 = dt2.astimezone(utc_tz)

            if u1.day != u2.day:
                if u1.day < u2.day:
                    # UTC start on previous day, end on reference day
                    result[(day_idx - 1) % 7].append(f"{_fmt_hm(u1)}-24:00")
                    result[day_idx].append(f"00:00-{_fmt_hm(u2)}")
                else:
                    # UTC start on reference day, end on next day
                    result[day_idx].append(f"{_fmt_hm(u1)}-24:00")
                    result[(day_idx + 1) % 7].append(f"00:00-{_fmt_hm(u2)}")
            else:
                if u1.day != 20:
                    shift = (day_idx + 1) % 7 if u1.day > 20 else (day_idx - 1) % 7
                    result[shift].append(f"{_fmt_hm(u1)}-{_fmt_hm(u2)}")
                else:
                    result[day_idx].append(f"{_fmt_hm(u1)}-{_fmt_hm(u2)}")

    return json.dumps(result)


def _periods_utc_json_to_local(periods_json: str, tz_name: str) -> dict:
    """
    Parse stored UTC period JSON and return {day_name: [{start, end}]} in local timezone.
    Mirrors PHP RecordingsController::recording_periods_from_utc().

    Key fixes vs the original Python port:
    - "24:00" stored UTC end is treated as next-day 00:00 UTC (matching PHP's DateTime("24:00")),
      then converted to local like any other endpoint.
    - Cross-midnight local periods are split into two parts (start→"24:00" / "00:00"→end).
    - Adjacent/overlapping periods are sorted and merged exactly as PHP does.
    """
    try:
        local_tz = ZoneInfo(tz_name)
    except Exception:
        local_tz = ZoneInfo("UTC")
    utc_tz = ZoneInfo("UTC")

    try:
        arrays = json.loads(periods_json)
    except (json.JSONDecodeError, TypeError):
        arrays = [[] for _ in range(7)]
    if not isinstance(arrays, list) or len(arrays) != 7:
        arrays = [[] for _ in range(7)]

    result: dict[str, list[dict]] = {day: [] for day in _DAYS}

    def _target(day_idx: int, local_dt: _dt) -> str:
        """Map a local datetime back to the correct display day."""
        if local_dt.day == 20:
            return _DAYS[day_idx]
        if local_dt.day > 20:
            return _DAYS[(day_idx + 1) % 7]
        return _DAYS[(day_idx - 1) % 7]

    for day_idx, day_name in enumerate(_DAYS):
        for period_str in arrays[day_idx]:
            parts = str(period_str).split("-", 1)
            if len(parts) != 2:
                continue
            s_raw, e_raw = parts[0].strip(), parts[1].strip()
            if not s_raw or not e_raw:
                continue

            # Normalise shorthand "0" → "00:00"
            if ":" not in s_raw:
                s_raw = f"{int(s_raw):02d}:00"

            try:
                sh, sm = map(int, s_raw.split(":"))
            except ValueError:
                continue

            dt1_utc = _dt(2016, 2, 20, sh, sm, tzinfo=utc_tz)

            # "24:00" stored UTC = next-day 00:00 UTC (mirrors PHP DateTime("2016-02-20 24:00"))
            if e_raw in ("24:00", "24"):
                dt2_utc = _dt(2016, 2, 21, 0, 0, tzinfo=utc_tz)
            else:
                try:
                    eh, em = map(int, e_raw.split(":"))
                except ValueError:
                    continue
                dt2_utc = _dt(2016, 2, 20, eh, em, tzinfo=utc_tz)

            l1 = dt1_utc.astimezone(local_tz)
            l2 = dt2_utc.astimezone(local_tz)

            if l1.day == l2.day:
                # Same local day — simple period
                result[_target(day_idx, l1)].append(
                    {"start": _fmt_hm(l1), "end": _fmt_hm(l2)}
                )
            else:
                # Period crosses local midnight — split into two parts
                t1 = _target(day_idx, l1)
                result[t1].append({"start": _fmt_hm(l1), "end": "24:00"})
                # Only add the second part if l2 is not exactly midnight
                if l2.hour != 0 or l2.minute != 0:
                    t2 = _target(day_idx, l2)
                    result[t2].append({"start": "00:00", "end": _fmt_hm(l2)})

    # Sort and merge adjacent periods — mirrors PHP's sort + adjacent-merge loop
    for day_key in _DAYS:
        periods = result[day_key]
        if len(periods) <= 1:
            continue
        periods.sort(key=lambda p: p["start"])
        merged: list[dict] = [periods[0]]
        for p in periods[1:]:
            last = merged[-1]
            if last["end"] == p["start"]:
                merged[-1] = {"start": last["start"], "end": p["end"]}
            else:
                merged.append(p)
        result[day_key] = merged

    return result


# ── Index (filter + results) ──────────────────────────────────────────────────

@router.get("")
async def recordings_index():
    return FileResponse(PUBLIC_DIR / "index.html")


# ── JSON search (for React frontend) ─────────────────────────────────────────

@router.get("/json", response_class=JSONResponse)
async def recordings_json(
    request: Request,
    session: dict = Depends(require_session),
    page: int = 1,
):
    params = dict(request.query_params)
    role   = session.get("role", "user")

    if role not in ("admin", "auditor"):
        params["participants"] = session.get("username", "")

    filters  = _build_filters(params)
    per_page = int(params.get("per_page", _PER_PAGE))

    try:
        raw = await atp_client.recording_search(filters, page=page, per_page=per_page)
        if isinstance(raw, dict):
            results = raw.get("results", [])
            total   = raw.get("total", len(results))
        else:
            results = list(raw)
            total   = len(results)
    except atp_client.AtpBackendError as exc:
        logger.error("Recording JSON search failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=502)

    total_pages = max(1, (total + per_page - 1) // per_page)

    # Normalise each recording to the frontend schema
    items = []
    for rec in results:
        items.append({
            "rec_id":     rec.get("id", rec.get("rec_id", "")),
            "start_time": rec.get("start_time", rec.get("date", "")),
            "owner":      rec.get("owner", rec.get("username", "")),
            "line":       rec.get("line", ""),
            "duration":   rec.get("duration", 0),
            "site":       rec.get("site", ""),
            "direction":  rec.get("direction", ""),
        })

    return {"items": items, "total": total, "pages": total_pages}


# ── Play (AJAX) ───────────────────────────────────────────────────────────────

@router.get("/play/{rec_id}", response_class=JSONResponse)
async def recordings_play(
    rec_id: str,
    session: dict = Depends(require_session),
):
    try:
        result = await atp_client.recording_play(rec_id)
        return {"url": result.get("url", f"/recordings/stream/{rec_id}")}
    except atp_client.AtpBackendError as exc:
        logger.error("Recording play failed: id=%r: %s", rec_id, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)


# ── Download selected as ZIP ──────────────────────────────────────────────────

@router.post("/download")
async def recordings_download(
    request: Request,
    session: dict = Depends(require_session),
):
    form   = await request.form()
    ids    = form.getlist("rec_ids")
    if not ids:
        return RedirectResponse(url="/recordings", status_code=303)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rec_id in ids:
            try:
                result = await atp_client.recording_download(rec_id)
                filename = result.get("filename", f"{rec_id}.wav")
                content  = result.get("content", b"")
                if isinstance(content, str):
                    content = content.encode()
                zf.writestr(filename, content)
                # Metadata sidecar
                meta = result.get("metadata", "")
                if meta:
                    zf.writestr(filename.rsplit(".", 1)[0] + ".txt",
                                meta if isinstance(meta, bytes) else meta.encode())
            except atp_client.AtpBackendError as exc:
                logger.error("Recording download failed: id=%r: %s", rec_id, exc)

    buf.seek(0)
    logger.info("Recordings ZIP downloaded: %d files by %r", len(ids), session.get("username"))
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=\"recordings.zip\""},
    )


# ── Download all (current filter) as ZIP ─────────────────────────────────────

@router.get("/download-all")
async def recordings_download_all(
    request: Request,
    session: dict = Depends(require_session),
):
    params  = dict(request.query_params)
    role    = session.get("role", "user")
    if role not in ("admin", "auditor"):
        params["participants"] = session.get("username", "")

    filters = _build_filters(params)
    try:
        raw = await atp_client.recording_search(filters, page=1, per_page=9999)
        results = raw.get("results", []) if isinstance(raw, dict) else list(raw)
    except atp_client.AtpBackendError:
        results = []

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rec in results:
            rec_id = rec.get("id", "")
            try:
                result   = await atp_client.recording_download(rec_id)
                filename = result.get("filename", f"{rec_id}.wav")
                content  = result.get("content", b"")
                if isinstance(content, str):
                    content = content.encode()
                zf.writestr(filename, content)
                meta = result.get("metadata", "")
                if meta:
                    zf.writestr(filename.rsplit(".", 1)[0] + ".txt",
                                meta if isinstance(meta, bytes) else meta.encode())
            except atp_client.AtpBackendError:
                pass

    buf.seek(0)
    logger.info("Recordings ZIP (all) downloaded by %r", session.get("username"))
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=\"recordings_all.zip\""},
    )


# ── Line permissions ──────────────────────────────────────────────────────────

@router.get("/line-permissions")
async def line_permissions_index():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.get("/line-permissions/{username}/edit")
async def line_permissions_edit_get():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/line-permissions/{username}/edit")
async def line_permissions_edit_post(
    username: str,
    request: Request,
    session: dict = Depends(require_session),
):
    form    = await request.form()
    allowed = form.getlist("allowed_lines")
    try:
        await atp_client.recording_line_permissions_set(username, allowed)
        logger.info("Line permissions updated: user=%r by %r", username, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Line perms update failed: user=%r: %s", username, exc)
    return RedirectResponse(url="/recordings/line-permissions", status_code=303)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_filters(params: dict) -> dict:
    """Extract filter parameters from the query string / form into a clean dict."""
    return {
        "site":           params.get("site", ""),
        "start_date":     params.get("start_date", ""),
        "end_date":       params.get("end_date", ""),
        "owner":          params.get("owner", ""),
        "vri":            params.get("vri", ""),
        "system":         params.get("system", ""),
        "line":           params.get("line", ""),
        "line_type":      params.get("line_type", ""),
        "participants":   params.get("participants", ""),
        "cli":            params.get("cli", ""),
        "forwarded_by":   params.get("forwarded_by", ""),
        "directions":     params.getlist("direction") if hasattr(params, "getlist") else
                          params.get("direction", ""),
        "statuses":       params.getlist("status") if hasattr(params, "getlist") else
                          params.get("status", ""),
        "audio_devices":  params.getlist("audio_device") if hasattr(params, "getlist") else
                          params.get("audio_device", ""),
        "audio_only":     params.get("audio_only", ""),
    }


# ── Veriant (VR) server helpers ────────────────────────────────────────────────

_VERIANT_COLS = ["id", "username", "password", "ip_address", "share", "mount", "destination", "gateway_ip"]


def _veriant_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("VERIANT_CONFIG_DIR", "/var/tmp/config-a1"))


def _veriant_db() -> pathlib.Path:
    return _veriant_dir() / "veriant.sqlite"


def _veriant_script(name: str) -> str:
    atp_apps = os.environ.get("ATP_APPS_DIR", "")
    datadir = os.environ.get("ATPMGR_DATADIR", "")
    if atp_apps:
        return str(pathlib.Path(atp_apps) / "bin" / name)
    if datadir:
        return str(pathlib.Path(datadir).parent / "atp-apps" / "bin" / name)
    return f"/opt/atp-apps/bin/{name}"


def _safe_read(path: pathlib.Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _ensure_veriant_db() -> sqlite3.Connection:
    d = _veriant_dir()
    d.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(_veriant_db()))
    con.execute("""
        CREATE TABLE IF NOT EXISTS servers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT NOT NULL DEFAULT '',
            password    TEXT NOT NULL DEFAULT '',
            ip_address  TEXT NOT NULL DEFAULT '',
            share       TEXT NOT NULL DEFAULT '',
            mount       TEXT NOT NULL DEFAULT '',
            destination TEXT NOT NULL DEFAULT '',
            gateway_ip  TEXT NOT NULL DEFAULT ''
        )
    """)
    con.commit()
    return con


def _veriant_row_to_dict(row: tuple) -> dict:
    return dict(zip(_VERIANT_COLS, row))


def _write_veriant_cfg(num: int, v: dict) -> None:
    path = _veriant_dir() / f"veriant-{num}.cfg"
    path.write_text(
        f"username:{v['username']}\n"
        f"password:{v['password']}\n"
        f"ipaddress:{v['ip_address']}\n"
        f"share:{v['share']}\n"
        f"mount:{v['mount']}\n"
        f"destination:{v['destination']}\n"
        f"gateway:{v['gateway_ip']}\n",
        encoding="utf-8",
    )
    try:
        path.chmod(0o666)
    except OSError:
        pass


def _delete_all_cfg_files() -> None:
    for f in _veriant_dir().glob("veriant-*.cfg"):
        try:
            f.chmod(0o666)
            f.unlink()
        except OSError:
            pass


def _recreate_all_cfg_files(rows: list) -> None:
    _delete_all_cfg_files()
    for i, v in enumerate(rows, start=1):
        _write_veriant_cfg(i, v)


async def _run_veriant_script(name: str, *args: str, timeout: int = 30) -> tuple:
    script = _veriant_script(name)
    try:
        proc = await asyncio.create_subprocess_exec(
            script, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, (out + err).decode(errors="replace").strip()
    except asyncio.TimeoutError:
        return 1, f"Script {name} timed out."
    except Exception as exc:
        return 1, str(exc)


def _all_veriant_rows(con: sqlite3.Connection) -> list:
    rows = con.execute(
        "SELECT id, username, password, ip_address, share, mount, destination, gateway_ip "
        "FROM servers ORDER BY id"
    ).fetchall()
    return [_veriant_row_to_dict(r) for r in rows]


# ── Voice Recording Servers (VRS) endpoints ───────────────────────────────────

@router.get("/vrs")
async def vrs_list(request: Request, session: dict = Depends(require_session)):
    """List all Voice Recording Servers."""
    from session import wants_json
    if not wants_json(request):
        return FileResponse(PUBLIC_DIR / "index.html")
    try:
        servers = await atp_client.vrs_list()
    except atp_client.AtpBackendError as exc:
        logger.error("vrs_list backend error: %s", exc)
        servers = []
    result = []
    for s in servers:
        if isinstance(s, dict):
            result.append({
                "name": s.get("name", ""),
                "ip":   s.get("ip", ""),
                "site": s.get("site", ""),
            })
        elif isinstance(s, str):
            result.append({"name": s, "ip": "", "site": ""})
    return JSONResponse(result)


@router.post("/vrs/add", response_class=JSONResponse)
async def vrs_add(request: Request, session: dict = Depends(require_session)):
    body = await request.json()
    name = (body.get("name") or "").strip()
    ip   = (body.get("ip") or "").strip()
    site = (body.get("site") or "").strip()
    if not name or not ip:
        return JSONResponse({"error": "Name and IP are required."}, status_code=400)
    try:
        await atp_client.vrs_create(name, ip, site)
        logger.info("VRS created: %r by %r", name, session.get("username"))
        return {"ok": True}
    except atp_client.AtpBackendError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@router.post("/vrs/{name}/delete", response_class=JSONResponse)
async def vrs_delete(name: str, session: dict = Depends(require_session)):
    try:
        await atp_client.vrs_delete(name)
        logger.info("VRS deleted: %r by %r", name, session.get("username"))
        return {"ok": True}
    except atp_client.AtpBackendError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


# ── VR Servers endpoints ───────────────────────────────────────────────────────

# NOTE: /servers/add must be registered BEFORE /servers/{server_id}/* so that
# FastAPI matches the literal "add" path before treating it as an integer id.

@router.get("/servers")
async def veriant_list(request: Request, session: dict = Depends(require_session)):
    from session import wants_json
    if not wants_json(request):
        return FileResponse(PUBLIC_DIR / "index.html")
    con = _ensure_veriant_db()
    rows = _all_veriant_rows(con)
    con.close()
    return JSONResponse(rows)


@router.post("/servers/add", response_class=JSONResponse)
async def veriant_add(
    username:    str = Form(...),
    password:    str = Form(...),
    ip_address:  str = Form(...),
    share:       str = Form(...),
    mount:       str = Form(...),
    destination: str = Form(...),
    gateway_ip:  str = Form(...),
    session: dict = Depends(require_session),
):
    data = {
        "username":    username.strip(),
        "password":    password.strip(),
        "ip_address":  ip_address.strip(),
        "share":       share.strip(),
        "mount":       mount.strip(),
        "destination": destination.strip(),
        "gateway_ip":  gateway_ip.strip(),
    }
    con = _ensure_veriant_db()
    cur = con.execute(
        "INSERT INTO servers (username, password, ip_address, share, mount, destination, gateway_ip) "
        "VALUES (?,?,?,?,?,?,?)",
        (data["username"], data["password"], data["ip_address"],
         data["share"], data["mount"], data["destination"], data["gateway_ip"]),
    )
    new_id = cur.lastrowid
    con.commit()
    rows = _all_veriant_rows(con)
    con.close()
    num = next((i + 1 for i, v in enumerate(rows) if v["id"] == new_id), len(rows))
    _write_veriant_cfg(num, data)
    logger.info("Veriant add id=%d by=%r", new_id, session.get("username"))
    return {"success": True, "id": new_id}


@router.get("/servers/{server_id}/edit", response_class=JSONResponse)
async def veriant_get(server_id: int, session: dict = Depends(require_session)):
    con = _ensure_veriant_db()
    row = con.execute(
        "SELECT id, username, password, ip_address, share, mount, destination, gateway_ip "
        "FROM servers WHERE id=?", (server_id,)
    ).fetchone()
    con.close()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Veriant server not found.")
    return _veriant_row_to_dict(row)


@router.post("/servers/{server_id}/edit", response_class=JSONResponse)
async def veriant_edit(
    server_id:   int,
    username:    str = Form(...),
    password:    str = Form(""),
    ip_address:  str = Form(...),
    share:       str = Form(...),
    mount:       str = Form(...),
    destination: str = Form(...),
    gateway_ip:  str = Form(...),
    session: dict = Depends(require_session),
):
    con = _ensure_veriant_db()
    if not con.execute("SELECT id FROM servers WHERE id=?", (server_id,)).fetchone():
        con.close()
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Veriant server not found.")

    if password.strip():
        con.execute(
            "UPDATE servers SET username=?, password=?, ip_address=?, share=?, mount=?, destination=?, gateway_ip=? WHERE id=?",
            (username.strip(), password.strip(), ip_address.strip(),
             share.strip(), mount.strip(), destination.strip(), gateway_ip.strip(), server_id),
        )
    else:
        con.execute(
            "UPDATE servers SET username=?, ip_address=?, share=?, mount=?, destination=?, gateway_ip=? WHERE id=?",
            (username.strip(), ip_address.strip(),
             share.strip(), mount.strip(), destination.strip(), gateway_ip.strip(), server_id),
        )
    con.commit()
    rows = _all_veriant_rows(con)
    con.close()
    _recreate_all_cfg_files(rows)
    logger.info("Veriant edit id=%d by=%r", server_id, session.get("username"))
    return {"success": True}


@router.post("/servers/{server_id}/delete", response_class=JSONResponse)
async def veriant_delete(server_id: int, session: dict = Depends(require_session)):
    con = _ensure_veriant_db()
    row = con.execute(
        "SELECT id, username, password, ip_address, share, mount, destination, gateway_ip "
        "FROM servers WHERE id=?", (server_id,)
    ).fetchone()
    if not row:
        con.close()
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Veriant server not found.")

    v = _veriant_row_to_dict(row)

    script = _veriant_script("veriant_delete.sh")
    if os.path.isfile(script):
        rc, output = await _run_veriant_script("veriant_delete.sh", v["gateway_ip"])
        if rc != 0:
            con.close()
            msg = _safe_read(pathlib.Path("/var/tmp/errors")) or output or "Delete script failed."
            return {"success": False, "message": msg}

    con.execute("DELETE FROM servers WHERE id=?", (server_id,))
    con.commit()
    rows = _all_veriant_rows(con)
    con.close()
    _recreate_all_cfg_files(rows)
    logger.info("Veriant delete id=%d by=%r", server_id, session.get("username"))
    return {"success": True, "message": "Veriant server deleted successfully."}


@router.post("/servers/delete-all", response_class=JSONResponse)
async def veriant_delete_all(session: dict = Depends(require_session)):
    _delete_all_cfg_files()
    con = _ensure_veriant_db()
    rows = _all_veriant_rows(con)
    con.close()

    _recreate_all_cfg_files(rows)

    if not rows:
        return {"success": True, "message": "Veriant Updated."}

    script = _veriant_script("veriant_update.sh")
    if os.path.isfile(script):
        rc, output = await _run_veriant_script("veriant_update.sh")
        if rc != 0:
            msg = _safe_read(pathlib.Path("/var/tmp/errors")) or output or "Update script failed."
            return {"success": False, "message": msg}

    logger.info("Veriant delete-all by=%r", session.get("username"))
    return {"success": True, "message": "Veriant server Updated successfully."}


# ── Recording periods ─────────────────────────────────────────────────────────

def _get_primary_timezone(settings: dict) -> str:
    """
    Extract primary_timezone from settings dict, defaulting to 'UTC'.
    Handles both flat {name: value} and structured {name: {value: ...}} dicts.
    """
    raw = settings.get("primary_timezone")
    if isinstance(raw, dict):
        tz = raw.get("value") or ""
    elif isinstance(raw, str):
        tz = raw
    else:
        tz = ""
    return tz.strip() or "UTC"


async def _fetch_settings_with_timezone() -> tuple[dict, str]:
    """Fetch global settings and return (settings, tz_name), logging the resolved timezone."""
    settings = await atp_client.settings_get()
    tz_name = _get_primary_timezone(settings)
    logger.info("Recording periods timezone resolved: %r (raw primary_timezone=%r)",
                tz_name, settings.get("primary_timezone"))
    return settings, tz_name


@router.get("/periods/timezone", response_class=JSONResponse)
async def recording_periods_timezone(session: dict = Depends(require_session)):
    """Debug endpoint: returns the resolved primary_timezone used for recording period conversion."""
    try:
        settings = await atp_client.settings_get()
        tz_name = _get_primary_timezone(settings)
        raw_val = settings.get("primary_timezone")
        return {"timezone": tz_name, "raw": raw_val}
    except atp_client.AtpBackendError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@router.get("/periods/json", response_class=JSONResponse)
async def recording_periods_get(session: dict = Depends(require_session)):
    """
    Load recording periods from global settings, converting stored UTC times to local
    using the primary_timezone setting.
    Ports: RecordingsController index (GET) + recording_periods_from_utc().
    Returns: {Sun: [{start, end}], Mon: [...], ...} in primary_timezone local time.
    """
    try:
        settings, tz_name = await _fetch_settings_with_timezone()
        raw = settings.get("recording_periods", {})
        if isinstance(raw, dict):
            raw = raw.get("value", "[[],[],[],[],[],[],[]]")
        elif not isinstance(raw, str):
            raw = "[[],[],[],[],[],[],[]]"
        return _periods_utc_json_to_local(raw, tz_name)
    except atp_client.AtpBackendError:
        return {day: [] for day in _DAYS}


@router.post("/periods", response_class=JSONResponse)
async def recording_periods_save(
    request: Request,
    session: dict = Depends(require_session),
):
    """
    Save recording periods — converts local times (primary_timezone) to UTC before persisting.
    Ports: RecordingsController index (POST) + recording_periods_to_utc().
    Body: {periods: {Sun: [{start, end}], ...}} in primary_timezone local time.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"success": False, "message": "Invalid JSON body."}, status_code=400)

    periods_by_day = body.get("periods", {})

    try:
        _, tz_name = await _fetch_settings_with_timezone()
    except atp_client.AtpBackendError:
        tz_name = "UTC"

    utc_json = _periods_local_to_utc_json(periods_by_day, tz_name)
    try:
        await atp_client.settings_update("recording", {"recording_periods": utc_json})
        logger.info("Recording periods saved by %r (tz=%r)", session.get("username"), tz_name)
        return {"success": True, "message": "Recording periods saved."}
    except atp_client.AtpBackendError as exc:
        logger.error("Recording periods save failed: %s", exc)
        return JSONResponse({"success": False, "message": str(exc)}, status_code=502)
