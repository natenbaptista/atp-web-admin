"""
routers/lines.py — /lines/*

Ports: atp-dev/src/manager/app/Controller/LinesController.php

Routes:
  GET  /lines                             → index (paginated list)
  GET  /lines/search                      → AJAX search → JSON
  GET  /lines/add                         → add form
  POST /lines/add                         → create line
  GET  /lines/{dn}/edit                   → edit form
  POST /lines/{dn}/edit                   → update line
  POST /lines/{dn}/delete                 → delete line
  POST /lines/import                      → parse CSV preview / confirm import
  GET  /lines/blacklist-report            → HTML or CSV report
  GET  /lines/groups and /lines/line-groups → SPA
  GET  /lines/groups/search and /lines/line-groups/search → JSON
  POST /lines/groups and /lines/line-groups/add → create line group
  POST /lines/groups/{main}/edit          → update line group
  POST /lines/groups/{main}/delete        → delete line group
"""

import asyncio
import csv
import io
import os
import pathlib
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse

import atp_client
from session import require_session
from logging_config import logger

router = APIRouter(prefix="/lines")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"

PAGE_SIZE = 20

_LINE_TYPES = [
    {"value": "Line",     "label": "Line"},
    {"value": "ARD",      "label": "ARD"},
    {"value": "MRD",      "label": "MRD"},
    {"value": "OPENHOOT", "label": "Open Hoot"},
]

_FORWARD_CONDITIONS = [
    {"value": "immediate_forwarding",   "label": "Forward immediately"},
    {"value": "on_no_answer",         "label": "Forward if no answer"},
    {"value": "on_busy",              "label": "Forward if busy"},
    {"value": "on_no_answer_or_busy", "label": "Forward if no answer or busy"},
]

_FWD_LEGACY = {
    "always": "immediate_forwarding",
    "busy": "on_busy",
    "no_answer": "on_no_answer",
    "unreachable": "on_no_answer_or_busy",
}


def _group_main(g) -> str:
    if not isinstance(g, dict):
        return ""
    return str(g.get("main_line") or g.get("main") or g.get("name") or "").strip()


def _group_subs(g) -> list:
    if not isinstance(g, dict):
        return []
    raw = g.get("sub_lines") if g.get("sub_lines") is not None else (
        g.get("subs") if g.get("subs") is not None else g.get("lines", [])
    )
    if isinstance(raw, str):
        return [s.strip() for s in raw.split(",") if s.strip()]
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, dict):
            val = item.get("name") or item.get("dn") or item.get("line") or item.get("linename")
            if val:
                out.append(str(val).strip())
        elif item is not None and str(item).strip():
            out.append(str(item).strip())
    return out


def _prefers_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    ctype = request.headers.get("content-type", "")
    return "application/json" in accept.lower() or "application/json" in ctype.lower()


async def _read_group_body(request: Request, main_fallback: str = "") -> tuple:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        main = str(body.get("main_line") or body.get("main") or main_fallback or "").strip()
        subs = body.get("sub_lines")
        if subs is None:
            subs = body.get("sub_lines[]")
        if subs is None:
            subs = body.get("subs")
        if subs is None:
            subs = []
        if isinstance(subs, str):
            subs = [s.strip() for s in subs.split(",") if s.strip()]
        elif isinstance(subs, list):
            subs = [str(s).strip() for s in subs if str(s).strip()]
        else:
            subs = []
        return main, subs
    form = await request.form()
    main = str(form.get("main_line") or form.get("main") or main_fallback or "").strip()
    subs = form.getlist("sub_lines") or form.getlist("sub_lines[]") or form.getlist("subs")
    return main, [str(s).strip() for s in subs if str(s).strip()]


async def _validate_nesting(main: str, subs: list, *, editing: bool = False):
    if not main:
        return JSONResponse({"result": "fail", "detail": "Main line is required."}, status_code=422)
    try:
        groups = await atp_client.line_group_search()
    except atp_client.AtpBackendError as exc:
        return JSONResponse({"result": "fail", "detail": str(exc)}, status_code=502)
    if not isinstance(groups, list):
        groups = []
    mains = set()
    all_subs = set()
    for g in groups:
        gm = _group_main(g)
        if gm:
            mains.add(gm)
        for s in _group_subs(g):
            all_subs.add(s)
    if main in all_subs:
        return JSONResponse(
            {"result": "fail", "detail": f"Line {main} is already a sub-line and cannot be used as a main line."},
            status_code=422,
        )
    if not editing and main in mains:
        return JSONResponse(
            {"result": "fail", "detail": f"Line {main} is already a main line of another group."},
            status_code=422,
        )
    other_mains = {m for m in mains if m != main}
    nested = [s for s in subs if s in other_mains]
    if nested:
        return JSONResponse(
            {"result": "fail", "detail": f"Cannot nest groups: {', '.join(nested)} is a main line of another group."},
            status_code=422,
        )
    if main in subs:
        return JSONResponse(
            {"result": "fail", "detail": "A main line cannot also be listed as a sub-line."},
            status_code=422,
        )
    return None


async def _groups_payload() -> list:
    try:
        groups = await atp_client.line_group_search()
    except atp_client.AtpBackendError:
        groups = []
    return groups if isinstance(groups, list) else []


async def _lg_add(request: Request, session: dict, json_always: bool):
    main_line, sub_lines = await _read_group_body(request)
    err = await _validate_nesting(main_line, sub_lines, editing=False)
    if err:
        return err
    try:
        await atp_client.line_group_create({"main_line": main_line, "sub_lines": sub_lines})
        logger.info("Line group created: main=%r by %r", main_line, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Line group create failed: %s", exc)
        return JSONResponse({"result": "fail", "detail": str(exc)}, status_code=502)
    if json_always or _prefers_json(request):
        return JSONResponse({"result": "success", "data": {"main_line": main_line, "sub_lines": sub_lines}})
    return RedirectResponse(url="/lines/line-groups", status_code=303)


async def _lg_edit(main_line: str, request: Request, session: dict, json_always: bool):
    body_main, sub_lines = await _read_group_body(request, main_fallback=main_line)
    main_line = (main_line or body_main).strip()
    err = await _validate_nesting(main_line, sub_lines, editing=True)
    if err:
        return err
    try:
        await atp_client.line_group_update({"main_line": main_line, "sub_lines": sub_lines})
        logger.info("Line group updated: main=%r by %r", main_line, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Line group update failed: %s", exc)
        return JSONResponse({"result": "fail", "detail": str(exc)}, status_code=502)
    if json_always or _prefers_json(request):
        return JSONResponse({"result": "success", "data": {"main_line": main_line, "sub_lines": sub_lines}})
    return RedirectResponse(url="/lines/line-groups", status_code=303)


async def _lg_delete(main_line: str, request: Request, session: dict, json_always: bool):
    if not main_line:
        body_main, _ = await _read_group_body(request)
        main_line = body_main
    if not main_line:
        return JSONResponse({"result": "fail", "detail": "Main line is required."}, status_code=422)
    try:
        await atp_client.line_group_delete(main_line)
        logger.info("Line group deleted: main=%r by %r", main_line, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Line group delete failed: %s", exc)
        return JSONResponse({"result": "fail", "detail": str(exc)}, status_code=502)
    if json_always or _prefers_json(request):
        return JSONResponse({"result": "success", "data": {"main_line": main_line}})
    return RedirectResponse(url="/lines/line-groups", status_code=303)


@router.get("")
async def lines_index():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.get("/search", response_class=JSONResponse)
async def lines_search(
    q: str = "",
    page: int = 1,
    per_page: int = PAGE_SIZE,
    session: dict = Depends(require_session),
):
    per_page = max(1, min(per_page, 200))
    page = max(1, page)
    raw = await _safe_line_search("")
    qn = (q or "").strip()
    if qn:
        ql = qn.lower()

        def _line_name(ln: dict) -> str:
            return str(ln.get("dn") or ln.get("name") or ln.get("linename") or "").strip()

        raw = [ln for ln in raw if _line_name(ln).lower().startswith(ql)]
    total = len(raw)
    start = (page - 1) * per_page
    page_slice = raw[start: start + per_page]
    items = await _lines_for_table(page_slice)
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
    }


@router.get("/names", response_class=JSONResponse)
async def lines_names(
    username: Optional[str] = None,
    session: dict = Depends(require_session),
):
    datadir = os.environ.get("ATPMGR_DATADIR", "")
    if not datadir:
        return []
    db_path = os.path.join(datadir, "config.sqlite")
    try:
        con = sqlite3.connect(db_path)
        if username:
            rows = con.execute(
                "SELECT DISTINCT vl.name, COALESCE(l.type, 'Line') as line_type"
                " FROM line_appearances la"
                " JOIN virtual_lines vl ON vl.line_name = la.line_name"
                " JOIN lines l ON l.name = la.line_name"
                " WHERE la.user_certificate_cn = ?"
                " ORDER BY vl.name",
                (username,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT DISTINCT vl.name, COALESCE(l.type, 'Line') as line_type"
                " FROM virtual_lines vl"
                " JOIN lines l ON vl.line_name = l.name"
                " ORDER BY vl.name",
            ).fetchall()
        con.close()
        items = [{"name": r[0], "type": r[1]} for r in rows if r[0]]
        virtual_parents = {
            n["name"].split("--", 1)[0] for n in items if "--" in n["name"]
        }
        return [
            n for n in items
            if "--" in n["name"] or n["name"] not in virtual_parents
        ]
    except Exception as exc:
        logger.warning("lines_names: could not query: %s", exc)
        return []


@router.post("/validate-name", response_class=JSONResponse)
async def lines_validate_name(
    request: Request,
    session: dict = Depends(require_session),
):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return {"error": "Line Name is required"}
    import re as _re
    if not _re.match(r"^[A-Za-z0-9\S]+$", name):
        return {"error": "No spaces allowed"}
    try:
        results = await atp_client.line_search(name)
        for line in results:
            if (line.get("name") or line.get("dn") or "").lower() == name.lower():
                return {"error": f'Line "{name}" already exists.'}
    except atp_client.AtpBackendError:
        pass
    try:
        msg = await atp_client.line_validate({
            "name": name,
            "description": "Line Description",
            "dn": "",
            "capacity": 1,
        })
        if msg and msg.lower() != "true":
            return {"error": msg}
    except atp_client.AtpBackendError as exc:
        logger.warning("line_validate backend error: %s", exc)
        return {"valid": True}
    return {"valid": True}


@router.get("/add")
async def lines_add_form():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/add")
async def lines_add_post(
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    data = dict(form)
    errors = _validate_line_form(data, is_add=True)
    new_users = form.getlist("online_users")
    if errors:
        return JSONResponse({"errors": errors}, status_code=422)
    line_name = (data.get("dn") or "").strip()
    if line_name:
        try:
            results = await atp_client.line_search(line_name)
            for line in results:
                if (line.get("name") or line.get("dn") or "").lower() == line_name.lower():
                    return JSONResponse(
                        {"errors": {"dn": f'Line "{line_name}" already exists.'}},
                        status_code=422,
                    )
        except atp_client.AtpBackendError:
            pass
    try:
        await atp_client.line_create(_form_to_payload(data, form, session))
        logger.info("Line created: %r by %r", data.get("dn"), session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Line create failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=502)
    line_dn = (data.get("dn") or "").strip()
    if line_dn and new_users:
        import asyncio as _asyncio
        try:
            await _asyncio.gather(*[atp_client.line_add_appearance(line_dn, u) for u in new_users])
        except atp_client.AtpBackendError as exc:
            logger.warning("User appearance add failed for new line %r: %s", line_dn, exc)
    return JSONResponse({"ok": True})


@router.post("/import", response_class=JSONResponse)
async def lines_import(
    request: Request,
    session: dict = Depends(require_session),
):
    content_type = request.headers.get("content-type", "")
    if "multipart" in content_type:
        form    = await request.form()
        upload  = form.get("file")
        if not upload or not hasattr(upload, "read"):
            return JSONResponse({"error": "No file provided."}, status_code=400)
        raw  = await upload.read()
        text = raw.decode("utf-8-sig", errors="replace")
        reader   = csv.DictReader(io.StringIO(text))
        preview  = []
        for row in reader:
            name        = (row.get("Line Name") or row.get("line_name") or "").strip()
            line_type   = (row.get("Type") or row.get("type") or "Line").strip()
            description = (row.get("Line Description") or row.get("description") or "").strip()
            capacity    = (row.get("Capacity") or row.get("capacity") or "1").strip()
            if name:
                preview.append({
                    "name": name, "type": line_type,
                    "description": description, "capacity": capacity,
                })
        return {"lines": preview, "count": len(preview)}
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON."}, status_code=400)
    lines_to_create = body.get("lines", [])
    if not lines_to_create:
        return JSONResponse({"error": "No lines to import."}, status_code=400)
    created = 0
    errors: list = []
    for row in lines_to_create:
        name        = (row.get("name") or "").strip()
        line_type   = (row.get("type") or "Line").strip()
        description = (row.get("description") or "").strip()
        capacity    = max(1, int(row.get("capacity") or 1))
        if not name:
            continue
        try:
            await atp_client.line_create({
                "name": name, "description": description,
                "type": line_type, "capacity": capacity,
                "online_users": [], "cascade_rules": [],
            })
            created += 1
        except atp_client.AtpBackendError as exc:
            errors.append(f"{name}: {exc}")
    logger.info("Lines imported: %d by %r", created, session.get("username"))
    return {
        "success": True,
        "created": created,
        "errors":  errors,
        "message": f"{created} line(s) imported." + (f" {len(errors)} failed." if errors else ""),
    }


# Line groups — registered BEFORE /{dn} routes
@router.get("/groups/search", response_class=JSONResponse)
@router.get("/line-groups/search", response_class=JSONResponse)
async def line_groups_search_json(session: dict = Depends(require_session)):
    return await _groups_payload()


@router.get("/groups")
@router.get("/line-groups")
async def line_groups_index():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/groups")
async def line_groups_alias_add(request: Request, session: dict = Depends(require_session)):
    return await _lg_add(request, session, True)


@router.post("/groups/{main_line}/edit")
async def line_groups_alias_edit(main_line: str, request: Request, session: dict = Depends(require_session)):
    return await _lg_edit(main_line, request, session, True)


@router.post("/groups/{main_line}/delete")
async def line_groups_alias_delete(main_line: str, request: Request, session: dict = Depends(require_session)):
    return await _lg_delete(main_line, request, session, True)


@router.post("/line-groups/add")
async def line_groups_add(request: Request, session: dict = Depends(require_session)):
    return await _lg_add(request, session, False)


@router.post("/line-groups/{main_line}/edit")
async def line_groups_edit(main_line: str, request: Request, session: dict = Depends(require_session)):
    return await _lg_edit(main_line, request, session, False)


@router.post("/line-groups/{main_line}/delete")
async def line_groups_delete(main_line: str, request: Request, session: dict = Depends(require_session)):
    return await _lg_delete(main_line, request, session, False)


@router.get("/{dn}/detail", response_class=JSONResponse)
async def lines_detail(
    dn: str,
    session: dict = Depends(require_session),
):
    try:
        line = await atp_client.line_get(dn)
    except atp_client.AtpBackendError as exc:
        logger.error("line_get failed for %r: %s", dn, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)
    if not line:
        return JSONResponse({"error": f"Line {dn!r} not found"}, status_code=404)
    if not line.get("dn"):
        line["dn"] = dn
    return line


@router.get("/{dn}/users", response_class=JSONResponse)
async def lines_users_on_line(
    dn: str,
    session: dict = Depends(require_session),
):
    try:
        return await atp_client.line_usernames_on_line(dn)
    except atp_client.AtpBackendError:
        return []


@router.get("/{dn}/edit")
async def lines_edit_form():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/{dn}/edit")
async def lines_edit_post(
    request: Request,
    dn: str,
    session: dict = Depends(require_session),
):
    form = await request.form()
    data = dict(form)
    data["dn"] = dn
    errors = _validate_line_form(data, is_add=False)
    new_users = set(form.getlist("online_users"))
    if errors:
        return JSONResponse({"errors": errors}, status_code=422)
    try:
        await atp_client.line_update(_form_to_payload(data, form, session))
        logger.info("Line updated: %r by %r", dn, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Line update failed: %r: %s", dn, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)
    try:
        current_users = set(await atp_client.line_usernames_on_line(dn))
        to_add = new_users - current_users
        to_remove = current_users - new_users
        import asyncio as _asyncio
        await _asyncio.gather(
            *[atp_client.line_add_appearance(dn, u) for u in to_add],
            *[atp_client.line_remove_appearance(dn, u) for u in to_remove],
        )
    except atp_client.AtpBackendError as exc:
        logger.warning("User appearance sync failed for line %r: %s", dn, exc)
    return JSONResponse({"ok": True})


@router.post("/{dn}/delete")
async def lines_delete(
    dn: str,
    session: dict = Depends(require_session),
):
    try:
        await atp_client.line_delete(dn)
        logger.info("Line deleted: %r by %r", dn, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Line delete failed: %r: %s", dn, exc)
    return RedirectResponse(url="/lines", status_code=303)


@router.get("/blacklist-report")
async def lines_blacklist_report(
    request: Request,
    format: str = "html",
    session: dict = Depends(require_session),
):
    try:
        entries = await atp_client.blacklist_search()
    except atp_client.AtpBackendError:
        entries = []
    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["CLI", "Date", "Count", "Line"])
        for e in entries:
            writer.writerow([e.get("cli", ""), e.get("date", ""), e.get("count", ""), e.get("line", "")])
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=blacklist_report.csv"},
        )
    return FileResponse(PUBLIC_DIR / "index.html")


async def _safe_line_search(q: str = "") -> list:
    try:
        return await atp_client.line_search(q)
    except atp_client.AtpBackendError:
        return []


async def _lines_for_table(raw_lines: list) -> list:
    async def enrich(ln: dict) -> dict:
        row = dict(ln)
        key = str(row.get("dn") or row.get("name") or row.get("linename") or "").strip()
        row["line_key"] = key
        if not row.get("dn"):
            row["dn"] = key
        users: list = []
        if key:
            try:
                users = await atp_client.line_usernames_on_line(key)
            except atp_client.AtpBackendError:
                pass
        row["online_users"] = users
        if "description" not in row and row.get("desc") is not None:
            row["description"] = row["desc"]
        return row
    if not raw_lines:
        return []
    return list(await asyncio.gather(*[enrich(l) for l in raw_lines]))


async def _safe_line_get(dn: str) -> Optional[dict]:
    try:
        lines = await atp_client.line_search(dn)
        exact = [l for l in lines if str(l.get("dn", "")) == dn]
        return exact[0] if exact else None
    except atp_client.AtpBackendError:
        return None


async def _safe_user_list() -> list:
    try:
        return await atp_client.user_search("")
    except atp_client.AtpBackendError:
        return []


async def _line_group_context() -> tuple:
    try:
        groups = await atp_client.line_group_search()
    except atp_client.AtpBackendError:
        groups = []
    all_lines = [l.get("dn", l.get("name", "")) for l in await _safe_line_search()]
    return groups, all_lines


def _validate_line_form(data: dict, is_add: bool) -> dict:
    errors: dict = {}
    dn = data.get("dn", "").strip()
    if is_add:
        if not dn:
            if (data.get("name") or "").strip():
                errors["dn"] = (
                    "Directory number (DN) is required in the first field (digits only); "
                    "'Line Name' is optional and does not replace DN."
                )
            else:
                errors["dn"] = "Directory number is required."
        elif not dn.isdigit():
            errors["dn"] = "Directory number must be numeric."
    fwd_enabled = data.get("forwarding_enabled") == "on"
    fwd_to = data.get("forward_to", "").strip()
    if fwd_enabled and not fwd_to:
        errors["forward_to"] = "Forward-to value is required when forwarding is enabled."
    return errors


def _b01(val: bool) -> int:
    return 1 if val else 0


def _normalize_forwarding_condition(raw: str) -> Optional[str]:
    s = (raw or "").strip()
    if not s:
        return None
    if s in _FWD_LEGACY:
        return _FWD_LEGACY[s]
    return s


def _form_to_payload(data: dict, form, session: Optional[dict] = None) -> dict:
    import re as _re
    cascade: dict = {}
    for key, value in form.multi_items():
        m = _re.match(r"^cascade_rules\[(\d+)\]\[(\w+)\]$", key)
        if m:
            i, field = int(m.group(1)), m.group(2)
            cascade.setdefault(i, {})
            cascade[i][field] = value
    cascade_rules = [cascade[i] for i in sorted(cascade)]
    online_users = form.getlist("online_users")
    dn = (data.get("dn") or "").strip()
    display_name = (data.get("name") or "").strip()
    line_name = dn if dn else display_name
    fwd_enabled = data.get("forwarding_enabled") == "on"
    fwd_raw = _normalize_forwarding_condition(data.get("forwarding_condition", ""))
    try:
        cap = int(data.get("capacity") or 1)
    except (TypeError, ValueError):
        cap = 1
    try:
        tx_vol = int(data.get("transmit_vol") or 100)
    except (TypeError, ValueError):
        tx_vol = 100
    try:
        rx_vol = int(data.get("receive_vol") or 100)
    except (TypeError, ValueError):
        rx_vol = 100
    payload: dict = {
        "name": line_name,
        "description": data.get("description", "") or "",
        "type": data.get("type", "Line"),
        "capacity": cap,
        "forwarding_enabled": _b01(fwd_enabled),
        "forward_to": (data.get("forward_to", "") or "").strip() if fwd_enabled else "",
        "require_external_on_call": _b01(data.get("require_external_on_call") == "on"),
        "do_not_record": _b01(data.get("do_not_record") == "on"),
        "has_virtual_lines": _b01(data.get("make_virtual") == "on"),
        "drop_call_ext_leave": _b01(data.get("drop_call_ext_leave") == "on"),
        "call_answer_indication": _b01(data.get("call_answer_indication") == "on"),
        "line_open_indication": _b01(data.get("line_open_indication") == "on"),
        "transmit_vol": tx_vol,
        "receive_vol": rx_vol,
        "online_users": online_users,
        "hunt_group": data.get("hunt_group", "") or "",
        "busy_on_dnd": _b01(data.get("busy_on_dnd") == "on"),
        "cascade_rules": cascade_rules,
    }
    if fwd_enabled and session:
        payload["forwarding_enabled_by"] = session.get("username", "") or ""
    if fwd_raw:
        payload["forwarding_condition"] = fwd_raw
    return payload
