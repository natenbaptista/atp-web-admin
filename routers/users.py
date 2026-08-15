"""
routers/users.py — /users/*

Ports: atp-dev/src/manager/app/Controller/UsersController.php

Routes:
  GET  /users                         → index (paginated list)
  GET  /users/search                  → AJAX search → JSON
  GET  /users/validate                → AJAX uniqueness check → "true" or error string
  GET  /users/add                     → add form
  POST /users/add                     → create user
  GET  /users/{username}/edit         → edit form
  POST /users/{username}/edit         → update user
  POST /users/{username}/delete       → delete user
  GET  /users/{username}/copy         → copy form (loads user data into add form)
  POST /users/{username}/copy         → execute copy
  GET  /users/{username}/buttons      → button layout editor
  POST /users/{username}/buttons      → save button layout
"""

import asyncio
import csv
import io
import json
import pathlib
import re
import zoneinfo
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

import atp_client
from session import get_session, require_session
from logging_config import logger

router = APIRouter(prefix="/users")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"


def _wants_json(request: Request) -> bool:
    """True when the caller prefers a JSON response (React SPA)."""
    return "application/json" in request.headers.get("accept", "")

PAGE_SIZE = 12  # matches PHP $page_limit = 12
_PAGE_NUMBER_START = 3136  # matches PHP hidden_number base for page labels

# Timezone list — passed to template for autocomplete datalist
_TIMEZONES = sorted(zoneinfo.available_timezones())

# Device types (from device_type.ctp — fuse/tablet)
_DEVICE_TYPES = [
    {"value": "fuse",   "label": "Fuse"},
    {"value": "tablet", "label": "Tablet/Touch"},
]

# Roles
_ROLES = [
    {"value": "Admin",   "label": "Admin"},
    {"value": "User",    "label": "User"},
    {"value": "Auditor", "label": "Auditor"},
]


# ── Index ─────────────────────────────────────────────────────────────────────

@router.get("")
async def users_index():
    return FileResponse(PUBLIC_DIR / "index.html")


# ── AJAX search ───────────────────────────────────────────────────────────────

@router.get("/search", response_class=JSONResponse)
async def users_search(
    q: str = "",
    session: dict = Depends(require_session),
):
    users = await _safe_user_search(q)
    return users


# ── User detail (search + settings merged) ────────────────────────────────────

@router.get("/detail/{username}", response_class=JSONResponse)
async def users_detail(
    username: str,
    session: dict = Depends(require_session),
):
    """Returns a single user record with turret settings merged in."""
    users = await _safe_user_search(username)
    user = next((u for u in users if u.get("username") == username), None)
    if not user:
        return JSONResponse({"detail": f'User "{username}" not found.'}, status_code=404)
    try:
        settings = await atp_client.user_settings_get(username)
        user = {**user, **settings}
    except atp_client.AtpBackendError:
        pass  # return base user without settings on error

    # Load page labels via controller_pages_search (not controller_settings_get)
    turret_login = user.get("turret_login", "")
    if turret_login:
        try:
            pages = await atp_client.pages_search(turret_login)
            for pg in pages:
                number = pg.get("number", 0)
                name = pg.get("name", "")
                idx = number - _PAGE_NUMBER_START  # 0-based index
                if 0 <= idx < 57:
                    user[f"page_label_{idx + 1}"] = name
        except atp_client.AtpBackendError:
            pass

    return user


# ── AJAX username uniqueness check ────────────────────────────────────────────

@router.get("/timezones", response_class=JSONResponse)
async def users_timezones(session: dict = Depends(require_session)):
    """Return sorted list of all available IANA timezone names."""
    return _TIMEZONES


@router.get("/validate", response_class=JSONResponse)
async def users_validate(
    username: str = "",
    session: dict = Depends(require_session),
):
    """Returns true (JSON) if username is available, or an error string."""
    if not username:
        return True
    existing = await _safe_user_search(username)
    exact = [u for u in existing if u.get("username", "").lower() == username.lower()]
    if exact:
        return f'Username "{username}" is already taken.'
    return True


@router.get("/validate-access-id", response_class=JSONResponse)
async def users_validate_access_id(
    turret_login: str = "",
    exclude_username: str = "",
    session: dict = Depends(require_session),
):
    """Returns true if the Access ID is free, or an error string if already in use."""
    if not turret_login:
        return True
    all_users = await _safe_user_search("")
    taken_by = [
        u for u in all_users
        if u.get("turret_login", "") == turret_login
        and u.get("username", "") != exclude_username
    ]
    if taken_by:
        return f"Turret {turret_login} login exists."
    return True


# ── Add ───────────────────────────────────────────────────────────────────────

@router.get("/add")
async def users_add_form():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/add")
async def users_add_post(
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    data = dict(form)
    errors = _validate_user_form(data, is_add=True)

    if errors:
        logger.warning("User add validation failed: %r", errors)
        if _wants_json(request):
            return JSONResponse({"errors": errors}, status_code=422)
        return RedirectResponse(url=request.url.path, status_code=303)

    try:
        await atp_client.user_create(_form_to_payload(form))
        username_new = data.get("username", "")
        role = data.get("role", "User")
        if role.lower() == "user":
            settings = _form_to_settings(form)
            await atp_client.user_settings_update(username_new, role, settings)
            turret_login = data.get("turret_login", "")
            if turret_login:
                await _save_page_labels(form, turret_login)
        logger.info("User created: %r by %r", username_new, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("User create failed: %s", exc)
        if _wants_json(request):
            return JSONResponse({"errors": {"__global": str(exc)}}, status_code=502)
        return RedirectResponse(url=request.url.path, status_code=303)

    return RedirectResponse(url="/users", status_code=303)


# ── Edit ──────────────────────────────────────────────────────────────────────

@router.get("/{username}/edit")
async def users_edit_form():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/{username}/edit")
async def users_edit_post(
    request: Request,
    username: str,
    session: dict = Depends(require_session),
):
    form = await request.form()
    data = dict(form)
    data["username"] = username  # preserve original username
    errors = _validate_user_form(data, is_add=False)

    if errors:
        logger.warning("User edit validation failed %r: %r", username, errors)
        if _wants_json(request):
            return JSONResponse({"errors": errors}, status_code=422)
        return RedirectResponse(url=request.url.path, status_code=303)

    try:
        payload = _form_to_payload(form, extra={"username": username})
        await atp_client.user_update(payload)
        # Turret settings only apply to User role (matches PHP save_user behaviour)
        role = data.get("role", "User")
        if role.lower() == "user":
            settings = _form_to_settings(form)
            await atp_client.user_settings_update(username, role, settings)
            turret_login = data.get("turret_login", "")
            if turret_login:
                await _save_page_labels(form, turret_login)
        logger.info("User updated: %r by %r", username, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("User update failed %r: %s", username, exc)
        if _wants_json(request):
            return JSONResponse({"errors": {"__global": str(exc)}}, status_code=502)
        return RedirectResponse(url=request.url.path, status_code=303)

    return RedirectResponse(url="/users", status_code=303)


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/{username}/delete")
async def users_delete(
    username: str,
    session: dict = Depends(require_session),
):
    if username == "root":
        return RedirectResponse(url="/users", status_code=303)
    try:
        await atp_client.user_delete(username)
        logger.info("User deleted: %r by %r", username, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Delete failed for user %r: %s", username, exc)
    return RedirectResponse(url="/users", status_code=303)


# ── Copy ──────────────────────────────────────────────────────────────────────

@router.get("/{username}/copy")
async def users_copy_form():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/{username}/copy")
async def users_copy_post(
    username: str,
    request: Request,
    session: dict = Depends(require_session),
):
    """Create a new user based on a copy of source user."""
    form = await request.form()
    data = dict(form)
    errors = _validate_user_form(data, is_add=True)

    if errors:
        logger.warning("User copy validation failed (src=%r): %r", username, errors)
        if _wants_json(request):
            return JSONResponse({"errors": errors}, status_code=422)
        return RedirectResponse(url=request.url.path, status_code=303)

    try:
        await atp_client.user_create(_form_to_payload(form))
        username_new = data.get("username", "")
        role = data.get("role", "User")
        if role.lower() == "user":
            settings = _form_to_settings(form)
            await atp_client.user_settings_update(username_new, role, settings)
            turret_login = data.get("turret_login", "")
            if turret_login:
                await _save_page_labels(form, turret_login)

        # Copy button layout from source user
        src_buttons = await _safe_button_search(username)
        if src_buttons:
            copyable = []
            for b in src_buttons:
                if not _is_copyable(b):
                    continue
                copyable.append({
                    **b,
                    "position": b.get("button_id", b.get("position", 0)),
                    "guid": 0,
                })
            if copyable:
                # Grant line appearances before adding line-type buttons
                line_names = {
                    btn.get("line_name") or btn.get("line", "")
                    for btn in copyable
                    if btn.get("type") in _LINE_TYPES
                }
                for line_name in line_names:
                    if line_name:
                        try:
                            await atp_client.line_add_appearance(line_name, username_new)
                        except atp_client.AtpBackendError as exc:
                            logger.warning("line_add_appearance failed %r/%r: %s", username_new, line_name, exc)

                _EXEMPT_TYPES = {"disabled", "speaker", "float", "divert", "onebuttondivert"}
                add_by_page: dict[int, list] = defaultdict(list)
                for btn in copyable:
                    add_by_page[btn["position"] // BUTTONS_PER_PAGE].append(btn)
                for page_idx in sorted(add_by_page.keys()):
                    page_btns = sorted(
                        add_by_page[page_idx],
                        key=lambda b: b.get("type", "disabled") in _EXEMPT_TYPES,
                    )
                    await atp_client.button_add_all(username_new, page_btns)

        logger.info("User copied: %r → %r by %r", username, username_new, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("User copy failed (src=%r): %s", username, exc)
        if _wants_json(request):
            return JSONResponse({"errors": {"__global": str(exc)}}, status_code=502)
        return RedirectResponse(url=request.url.path, status_code=303)

    return RedirectResponse(url="/users", status_code=303)


# ── LDAP sync ─────────────────────────────────────────────────────────────────

@router.post("/ldap-sync", response_class=JSONResponse)
async def users_ldap_sync(session: dict = Depends(require_session)):
    try:
        message = await atp_client.ldap_sync()
        logger.info("LDAP sync by=%r", session.get("username"))
        return {"success": True, "message": message or "LDAP sync complete."}
    except atp_client.AtpBackendError as exc:
        return {"success": False, "message": str(exc)}


# ── Import users CSV ──────────────────────────────────────────────────────────

@router.post("/import", response_class=JSONResponse)
async def users_import(
    request: Request,
    session: dict = Depends(require_session),
):
    """
    Accept JSON body: {"users": [{username, password, first_name, last_name, turret_login, turret_pin}]}
    Or multipart CSV upload.
    Phase 1 (no confirm): parse CSV and return preview rows.
    Phase 2 (confirm=true): create all users.
    """
    ct = request.headers.get("content-type", "")
    if "application/json" in ct:
        body = await request.json()
        users_data = body.get("users", [])
        imported, errors = 0, []
        for u in users_data:
            try:
                await atp_client.user_create({
                    "username":      u.get("username", ""),
                    "password":      u.get("password", ""),
                    "first_name":    u.get("first_name", ""),
                    "last_name":     u.get("last_name", ""),
                    "role":          "User",
                    "turret_login":  u.get("turret_login", ""),
                    "turret_pin":    u.get("turret_pin", ""),
                })
                imported += 1
            except atp_client.AtpBackendError as exc:
                errors.append({"username": u.get("username", ""), "error": str(exc)})
        logger.info("Bulk import: %d imported %d errors by=%r", imported, len(errors), session.get("username"))
        return {"imported": imported, "errors": errors}
    else:
        # multipart: parse CSV, return preview
        form = await request.form()
        file = form.get("file")
        if not file:
            return JSONResponse({"error": "No file provided."}, status_code=400)
        raw = (await file.read()).decode(errors="replace")
        reader = csv.reader(io.StringIO(raw))
        rows = list(reader)
        if not rows:
            return JSONResponse({"error": "Empty CSV."}, status_code=400)
        # Skip header row; format: User Name, Trid ID, Trid Passcode, First Name, Last Name, Password
        preview = []
        for row in rows[1:]:
            if len(row) < 6 or not any(row):
                continue
            preview.append({
                "username":     row[0].strip(),
                "turret_login": row[1].strip(),
                "turret_pin":   row[2].strip(),
                "first_name":   row[3].strip(),
                "last_name":    row[4].strip(),
                "password":     row[5].strip(),
            })
        return {"preview": preview}


# ── Import as speeddial ───────────────────────────────────────────────────────

@router.post("/import-speeddial", response_class=JSONResponse)
async def users_import_speeddial(
    request: Request,
    session: dict = Depends(require_session),
):
    body = await request.json()
    username  = body.get("username", "")
    position  = int(body.get("position", 1)) - 1   # 0-based
    label     = body.get("label", "")
    speedial  = body.get("speed_dial", "")
    if not username or not speedial:
        return JSONResponse({"error": "username and speed_dial are required."}, status_code=400)
    try:
        await atp_client.button_update(username, [{
            "position":          position,
            "type":              2,          # speed_dial
            "label":             label,
            "line_name":         "",
            "hunt_group_id":     0,
            "speed_dial_number": speedial,
            "float": 0, "ring": 0, "privacy": 0,
            "ringtone": "default", "forward_condition": "always", "button_colour": "",
        }])
        return {"success": True}
    except atp_client.AtpBackendError as exc:
        return {"success": False, "message": str(exc)}


# ── Check button conflicts ────────────────────────────────────────────────────

@router.post("/check-button-conflicts", response_class=JSONResponse)
async def users_check_button_conflicts(
    request: Request,
    session: dict = Depends(require_session),
):
    """Return a list of buttons that already exist at the requested positions."""
    body      = await request.json()
    username  = body.get("username", "")
    positions = {int(p) for p in body.get("positions", [])}
    if not username or not positions:
        return []
    buttons = await _safe_button_search(username)
    conflicts = [b for b in buttons if int(b.get("position", -1)) in positions]
    return conflicts


# ── Move buttons ──────────────────────────────────────────────────────────────

@router.post("/move-buttons", response_class=JSONResponse)
async def users_move_buttons(
    request: Request,
    session: dict = Depends(require_session),
):
    """
    Reposition buttons on the same user.
    Body: {username, buttons: [{...}], new_start_position: 0-based}
    Buttons are placed starting at new_start_position, preserving relative order.
    """
    body             = await request.json()
    username         = body.get("username", "")
    buttons          = body.get("buttons", [])
    new_start        = int(body.get("new_start_position", 0))
    if not username or not buttons:
        return JSONResponse({"error": "username and buttons are required."}, status_code=400)

    target_positions = [new_start + i for i in range(len(buttons))]
    # Delete any existing buttons at target positions
    await atp_client.button_delete_positions(username, target_positions)

    updated = []
    for i, btn in enumerate(buttons):
        new_pos = new_start + i
        updated.append({**btn, "position": new_pos, "guid": ""})  # new guid = add
    try:
        await atp_client.button_update(username, updated)
        return {"success": True}
    except atp_client.AtpBackendError as exc:
        return {"success": False, "message": str(exc)}


# ── Replicate buttons ─────────────────────────────────────────────────────────

@router.post("/replicate-buttons", response_class=JSONResponse)
async def users_replicate_buttons(
    request: Request,
    session: dict = Depends(require_session),
):
    """
    Replicate a set of buttons to multiple pages of the same user.
    Body: {username, buttons, target_pages, new_start_position: 0-based, conflict_action: "skip"|"overwrite"}
    """
    BUTTONS_PER_PAGE = 56
    body             = await request.json()
    username         = body.get("username", "")
    buttons          = body.get("buttons", [])
    target_pages     = [int(p) for p in body.get("target_pages", [])]
    new_start_pos    = int(body.get("new_start_position", 0))
    conflict_action  = body.get("conflict_action", "skip")

    if not username or not buttons or not target_pages:
        return JSONResponse({"error": "username, buttons and target_pages are required."}, status_code=400)

    existing_buttons = await _safe_button_search(username)
    existing_by_pos  = {int(b.get("position", -1)): b for b in existing_buttons}

    success_pages, skipped_pages, replicated_count = [], [], 0
    first_rel0 = int(buttons[0].get("position", 0)) % BUTTONS_PER_PAGE
    new_start_rel0 = new_start_pos % BUTTONS_PER_PAGE

    for page_num in target_pages:
        page_offset0 = (page_num - 1) * BUTTONS_PER_PAGE
        to_delete, to_update = [], []
        for btn in buttons:
            orig_rel0   = int(btn.get("position", 0)) % BUTTONS_PER_PAGE
            delta       = orig_rel0 - first_rel0
            target_abs0 = page_offset0 + new_start_rel0 + delta
            target_rel0 = target_abs0 - page_offset0
            if target_rel0 < 0 or target_rel0 >= BUTTONS_PER_PAGE:
                continue
            target_pos = target_abs0  # 0-based
            if target_pos in existing_by_pos:
                if conflict_action == "skip":
                    continue
                to_delete.append(target_pos)
            if btn.get("type") == 0 or btn.get("type") == "disabled":
                to_delete.append(target_pos)
                continue
            to_update.append({**btn, "position": target_pos, "guid": ""})

        try:
            if to_delete:
                await atp_client.button_delete_positions(username, to_delete)
            if to_update:
                await atp_client.button_update(username, to_update)
                success_pages.append(page_num)
                replicated_count += len(to_update)
        except atp_client.AtpBackendError:
            skipped_pages.append(page_num)

    return {
        "success": True,
        "success_pages": success_pages,
        "skipped_pages": skipped_pages,
        "replicated_count": replicated_count,
    }


_LINE_TYPES = {"line", "ard", "mrd", "open_hoot", "onebuttondivert"}


def _is_copyable(btn: dict) -> bool:
    """Return True if a button should be included when copying (not disabled, has required data)."""
    btn_type = btn.get("type", "disabled")
    if btn_type == "disabled":
        return False
    line_name = btn.get("line_name") or btn.get("line", "")
    if btn_type in _LINE_TYPES and not line_name.strip():
        return False
    speed = btn.get("speed_dial_number") or btn.get("speedial_no", "")
    if btn_type == "speed_dial" and not speed.strip():
        return False
    return True


# ── Copy buttons ──────────────────────────────────────────────────────────────

@router.post("/copy-buttons", response_class=JSONResponse)
async def users_copy_buttons(
    request: Request,
    session: dict = Depends(require_session),
):
    """
    Copy buttons to one or more other users.
    Body: {from_username, to_usernames: [], buttons: [{...}], new_start_position}
    """
    BUTTONS_PER_PAGE = 56
    body          = await request.json()
    to_usernames  = body.get("to_usernames", [])
    buttons       = body.get("buttons", [])
    new_start     = body.get("new_start_position")   # None = preserve original positions

    if new_start is None:
        # No offset given — copy each button to its original absolute position
        # (same page, same slot) on the target user. Clear whole source page first.
        src_offset = ((buttons[0]["position"] // BUTTONS_PER_PAGE) * BUTTONS_PER_PAGE
                      if buttons else 0)
        delete_positions = list(range(src_offset, src_offset + BUTTONS_PER_PAGE))
        to_add = [{**btn, "guid": 0} for btn in buttons if _is_copyable(btn)]
    else:
        # Offset given — map each button's relative slot to the new page start,
        # preserving gaps so the grid layout is maintained.
        new_start    = int(new_start)
        src_offset   = ((buttons[0]["position"] // BUTTONS_PER_PAGE) * BUTTONS_PER_PAGE
                        if buttons else 0)
        delete_positions = list(range(new_start, new_start + BUTTONS_PER_PAGE))
        to_add = [
            {**btn, "position": new_start + (btn["position"] - src_offset), "guid": 0}
            for btn in buttons if _is_copyable(btn)
        ]

    errors = []
    for target_user in to_usernames:
        await atp_client.button_delete_positions(target_user, delete_positions)
        try:
            if to_add:
                await atp_client.button_add_all(target_user, to_add)
        except atp_client.AtpBackendError as exc:
            errors.append({"username": target_user, "error": str(exc)})
    return {"success": len(errors) == 0, "errors": errors}


# ── Copy page ─────────────────────────────────────────────────────────────────

@router.post("/copy-page", response_class=JSONResponse)
async def users_copy_page(
    request: Request,
    session: dict = Depends(require_session),
):
    """
    Copy a page (or all pages) of buttons to target pages on the same user.
    Body: {username, buttons: [{...}], target_pages: [2,3], all_pages: false}
    """
    BUTTONS_PER_PAGE = 56
    body          = await request.json()
    username      = body.get("username", "")
    buttons       = body.get("buttons", [])   # buttons on the source page (page-relative)
    target_pages  = [int(p) for p in body.get("target_pages", [])]
    all_pages     = bool(body.get("all_pages", False))

    if all_pages:
        target_pages = list(range(1, 57))  # pages 1-56

    errors = []
    for page_num in target_pages:
        offset = (page_num - 1) * BUTTONS_PER_PAGE
        delete_positions = list(range(offset, offset + BUTTONS_PER_PAGE))
        await atp_client.button_delete_positions(username, delete_positions)
        to_add = [
            {**btn, "position": offset + (btn.get("position", 0) % BUTTONS_PER_PAGE), "guid": 0}
            for btn in buttons if _is_copyable(btn)
        ]
        try:
            if to_add:
                await atp_client.button_add_all(username, to_add)
        except atp_client.AtpBackendError as exc:
            errors.append({"page": page_num, "error": str(exc)})
    return {"success": len(errors) == 0, "errors": errors}


# ── Copy layout ───────────────────────────────────────────────────────────────

@router.post("/copy-layout", response_class=JSONResponse)
async def users_copy_layout(
    request: Request,
    session: dict = Depends(require_session),
):
    """
    Copy turret layout (and optional speaker channel) to other users.
    Body: {to_usernames: [], layout_string, device_type, channel_value}
    """
    body          = await request.json()
    to_usernames  = body.get("to_usernames", [])
    layout_string = body.get("layout_string", "")
    device_type   = body.get("device_type", "tablet")
    channel_value = body.get("channel_value", "")
    key = "tablet_turret_layout" if device_type == "tablet" else "fuse_turret_layout"

    errors = []
    for uname in to_usernames:
        try:
            await atp_client.user_settings_update(uname, "User", {key: layout_string})
            if channel_value:
                await atp_client.user_speaker_setting(uname, channel_value)
        except atp_client.AtpBackendError as exc:
            errors.append({"username": uname, "error": str(exc)})
    return {"success": len(errors) == 0, "errors": errors}


# ── Buttons ───────────────────────────────────────────────────────────────────

BUTTONS_PER_PAGE = 56  # 8 columns × 7 rows
BUTTON_PAGES_DEFAULT = 4  # shown when backend has no buttons

# String type name → C++ ButtonType enum integer (button_types.h)
_BUTTON_TYPE_INT: dict[str, int] = {
    "disabled":        0,
    "line":            1,
    "speed_dial":      2,
    "page_shortcut":   4,
    "ard":             5,
    "wildcard":        6,
    "onebuttondivert": 10,
    "timezone":        11,
    "float":           12,
    "speaker":         13,
    "macro":           14,
    "mrd":             15,
    "intercom":        16,
    "open_hoot":       17,
    "macro_sequence":  18,
}

_BUTTON_TYPES = [
    {"value": "disabled",        "label": "Disabled"},
    {"value": "line",            "label": "Line"},
    {"value": "speed_dial",      "label": "Speed Dial"},
    {"value": "onebuttondivert", "label": "One-Button Divert"},
    {"value": "page_shortcut",   "label": "Page Shortcut"},
    {"value": "ard",             "label": "ARD"},
    {"value": "wildcard",        "label": "Wildcard"},
    {"value": "timezone",        "label": "Timezone"},
    {"value": "mrd",             "label": "MRD"},
    {"value": "intercom",        "label": "Intercom"},
    {"value": "open_hoot",       "label": "Open Hoot"},
    {"value": "macro_sequence",  "label": "Macro Sequence"},
]

_BUTTON_KEY_RE = re.compile(r"^buttons\[(\d+)\]\[(\w+)\]$")


@router.get("/{username}/buttons")
async def users_buttons_form():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.get("/{username}/buttons/json", response_class=JSONResponse)
async def users_buttons_json(
    username: str,
    session: dict = Depends(require_session),
):
    """Return saved button list for a user as a flat JSON array."""
    return await _safe_button_search(username)


@router.post("/{username}/buttons/partial", response_class=JSONResponse)
async def users_buttons_partial(
    request: Request,
    username: str,
    session: dict = Depends(require_session),
):
    """Save only the specified (dirty) buttons.  Frontend sends only positions
    that were actually edited since the last load."""
    body = await request.json()
    raw_buttons = body.get("buttons", [])

    if not raw_buttons:
        return JSONResponse({"success": True, "page_errors": []})

    def _map_btn(b: dict) -> dict:
        """Map frontend ButtonConfig field names → C++ field names."""
        return {
            "position":          int(b.get("position", 0)),
            "type":              b.get("type", "disabled"),
            "label":             b.get("label", ""),
            "line_name":         b.get("line", ""),
            "hunt_group_id":     (lambda v: v if v else None)(int(b.get("hunt_group", 0) or 0)),
            "speed_dial_number": b.get("speedial_no", ""),
            "float":             1 if b.get("show_in_floating_area") else 0,
            "ring":              1 if b.get("audio_ring") else 0,
            "privacy":           1 if b.get("default_private") else 0,
            "ringtone":          b.get("ringtone", "") if b.get("ringtone", "") != "default" else "",
            "forward_condition": b.get("forward_condition", "always"),
            "button_colour":     b.get("button_colour", ""),
        }

    incoming     = [_map_btn(b) for b in raw_buttons]
    dirty_positions = {b["position"] for b in incoming}

    logger.info("Partial button save: user=%r positions=%s", username, sorted(dirty_positions))

    page_errors: list[str] = []
    try:
        # Load existing buttons only at dirty positions for diff.
        # C++ button_search returns "button_id" for the position field (not "position").
        all_existing = await _safe_button_search(username)
        existing_by_pos = {
            int(b.get("button_id", b.get("position", -1))): b
            for b in all_existing
            if b.get("guid") and int(b.get("button_id", b.get("position", -1))) in dirty_positions
        }
        # Only non-disabled buttons need to be added
        new_by_pos = {b["position"]: b for b in incoming if b["type"] != "disabled"}

        _CMP = ("type", "label", "line_name", "hunt_group_id", "speed_dial_number",
                "float", "ring", "privacy", "ringtone", "forward_condition", "button_colour")

        def _sig(b):
            return tuple(str(b.get(f, "")).strip() for f in _CMP)

        to_delete_guids: list[str] = []
        to_add:          list[dict] = []

        for pos, old in existing_by_pos.items():
            new = new_by_pos.get(pos)
            if new is None:
                to_delete_guids.append(str(old["guid"]))
            elif _sig(old) != _sig(new):
                to_delete_guids.append(str(old["guid"]))
                to_add.append(new)

        for pos, new in new_by_pos.items():
            if pos not in existing_by_pos:
                to_add.append(new)

        logger.info(
            "Partial button diff: user=%r existing=%d incoming=%d delete=%d add=%d",
            username, len(existing_by_pos), len(new_by_pos),
            len(to_delete_guids), len(to_add),
        )

        if to_delete_guids:
            await atp_client.button_remove_guids(username, to_delete_guids)

        if to_add:
            _EXEMPT_TYPES = {"disabled", "speaker", "float", "divert", "onebuttondivert"}
            add_by_page: dict[int, list] = defaultdict(list)
            for btn in to_add:
                add_by_page[btn["position"] // BUTTONS_PER_PAGE].append(btn)

            for page_idx in sorted(add_by_page.keys()):
                page_btns = sorted(
                    add_by_page[page_idx],
                    key=lambda b: b.get("type", "disabled") in _EXEMPT_TYPES,
                )
                try:
                    await atp_client.button_add_all(username, page_btns)
                except atp_client.AtpBackendError as exc:
                    page_errors.append(f"page {page_idx + 1}: {exc}")
                    logger.warning("Partial button add error for user=%r %s", username, page_errors[-1])

        logger.info("Partial button save complete: user=%r", username)
    except atp_client.AtpBackendError as exc:
        logger.error("Partial button save failed for user=%r: %s", username, exc)
        page_errors = [str(exc)]

    return JSONResponse({"success": not page_errors, "page_errors": page_errors})


@router.post("/{username}/buttons")
async def users_buttons_post(
    request: Request,
    username: str,
    page: int = 1,
    session: dict = Depends(require_session),
):
    form = await request.form()
    buttons = _parse_buttons_form(form, page)

    logger.info("Button save request: user=%r page=%d total=%d", username, page, len(buttons))

    page_errors: list[str] = []
    try:
        # Filter to only active (non-disabled, non-empty) buttons from the incoming form.
        active_incoming = [
            btn for btn in buttons
            if btn.get("type", "disabled") != "disabled"
            and not (btn.get("type") in _LINE_TYPES and not btn.get("line_name", "").strip())
            and not (btn.get("type") == "speed_dial" and not btn.get("speed_dial_number", "").strip())
        ]

        # Diff against existing buttons — only send what actually changed.
        # This avoids timing out the ATP backend when only 1 button was edited.
        existing = await _safe_button_search(username)
        # C++ button_search returns "button_id" for the position field (not "position").
        existing_by_pos = {int(b.get("button_id", b.get("position", -1))): b for b in existing if b.get("guid")}
        new_by_pos      = {btn["position"]: btn for btn in active_incoming}

        _CMP = ("type", "label", "line_name", "hunt_group_id", "speed_dial_number",
                "float", "ring", "privacy", "ringtone", "forward_condition", "button_colour")

        def _sig(b):
            return tuple(str(b.get(f, "")).strip() for f in _CMP)

        to_delete_guids: list[str] = []
        to_add:          list[dict] = []

        # Positions removed or whose data changed → delete old GUID, re-add with new data
        for pos, old in existing_by_pos.items():
            new = new_by_pos.get(pos)
            if new is None:
                to_delete_guids.append(str(old["guid"]))       # button removed
            elif _sig(old) != _sig(new):
                to_delete_guids.append(str(old["guid"]))       # data changed — delete old
                to_add.append(new)                              #              — add new

        # Brand-new positions (didn't exist before)
        for pos, new in new_by_pos.items():
            if pos not in existing_by_pos:
                to_add.append(new)

        logger.info(
            "Button save diff: user=%r existing=%d incoming=%d delete=%d add=%d",
            username, len(existing_by_pos), len(new_by_pos),
            len(to_delete_guids), len(to_add),
        )

        # Deletions are constraint-free — send as one batch.
        if to_delete_guids:
            await atp_client.button_remove_guids(username, to_delete_guids)

        # Additions: add page-by-page (≤56 buttons per ATP call).
        # The C++ checks each button against previously-inserted buttons in the
        # same call, so a large batch can trigger false "same-page" constraint
        # errors.  Page-by-page also gives finer error reporting.
        if to_add:
            add_by_page: dict[int, list] = defaultdict(list)
            for btn in to_add:
                add_by_page[btn["position"] // BUTTONS_PER_PAGE].append(btn)

            # Types that are exempt from the C++ line-duplicate check (divert-like).
            # The C++ processes buttons sequentially; if an exempt type (OBD/divert) is
            # inserted BEFORE a non-exempt type (LINE) that shares the same line, the
            # LINE's check finds the OBD and errors.  Adding non-exempt types first
            # (when the page is still empty) prevents the false conflict.
            _EXEMPT_TYPES = {"disabled", "speaker", "float", "divert", "onebuttondivert"}

            page_errors: list[str] = []
            for page_idx in sorted(add_by_page.keys()):
                # Sort: non-exempt (LINE, ARD, …) first; exempt (OBD, divert, …) last
                page_btns = sorted(
                    add_by_page[page_idx],
                    key=lambda b: b.get("type", "disabled") in _EXEMPT_TYPES,
                )
                try:
                    await atp_client.button_add_all(username, page_btns)
                except atp_client.AtpBackendError as exc:
                    page_errors.append(f"page {page_idx + 1}: {exc}")
                    logger.warning("Button add error for user=%r %s", username, page_errors[-1])

            if page_errors:
                logger.error("Button save partial errors for user=%r: %s", username, "; ".join(page_errors))

        logger.info("Button save complete: user=%r", username)
    except atp_client.AtpBackendError as exc:
        logger.error("Button save failed for user=%r: %s", username, exc)
        page_errors = [str(exc)]

    if _wants_json(request):
        return JSONResponse({
            "success": not page_errors,
            "page_errors": page_errors,
        })
    return RedirectResponse(
        url=f"/users/{username}/buttons?page={page}", status_code=303
    )


def _prepare_button_context(all_buttons: list, page: int) -> tuple[int, str]:
    """Return (total_pages, buttons_json) where buttons_json is a
    JSON object keyed by 0-based page-relative position (string keys)."""
    if all_buttons:
        max_pos = max((b.get("position", 1) for b in all_buttons), default=1)
        total_pages = max(BUTTON_PAGES_DEFAULT, (max_pos + BUTTONS_PER_PAGE - 1) // BUTTONS_PER_PAGE)
    else:
        total_pages = BUTTON_PAGES_DEFAULT

    page = max(1, min(page, total_pages))
    first = (page - 1) * BUTTONS_PER_PAGE + 1  # 1-based absolute start
    last  = first + BUTTONS_PER_PAGE - 1        # 1-based absolute end

    page_buttons: dict[str, dict] = {}
    for b in all_buttons:
        abs_pos = b.get("position", 0)
        if first <= abs_pos <= last:
            rel = str(abs_pos - first)  # 0-based string key for JS
            page_buttons[rel] = {
                "type":             b.get("type", "disabled"),
                "label":            b.get("label", ""),
                "line_id":          str(b.get("line_id", "")),
                "hunt_group":       b.get("hunt_group", ""),
                "speed_dial_number": b.get("speed_dial_number", b.get("speedial_no", "")),
                "page_number":      str(b.get("page_number", "")),
            }

    return total_pages, json.dumps(page_buttons)


def _parse_buttons_form(form, page: int) -> list[dict]:  # noqa: ARG001 (page unused — frontend sends all buttons)
    """Reconstruct full button list from flat form data buttons[i][field].

    The frontend sends ALL pages at once (indices 0-223), so we process
    every entry and ignore the page param.  Field names are mapped to the
    C++ ButtonUpdateRequest / ButtonConfiguration names here.
    """
    raw: dict[int, dict] = {}
    for key, value in form.multi_items():
        m = _BUTTON_KEY_RE.match(key)
        if m:
            i, field = int(m.group(1)), m.group(2)
            raw.setdefault(i, {})
            raw[i][field] = value

    total = max(raw.keys(), default=-1) + 1 if raw else BUTTONS_PER_PAGE
    result = []
    for i in range(total):
        btn = raw.get(i, {})
        type_str = btn.get("type", "disabled")
        result.append({
            "position":          i,
            "type":              type_str,
            "label":             btn.get("label", ""),
            # form key "line"       → C++ field "line_name"
            "line_name":         btn.get("line", ""),
            # form key "hunt_group" → C++ field "hunt_group_id".
            # Send None (→ JSON null) when 0 so the C++ stores NULL.
            # SQLite UNIQUE allows multiple NULLs; integer 0 would conflict for
            # multiple speakers with no hunt group assigned.
            "hunt_group_id":     (lambda v: v if v else None)(int(btn.get("hunt_group", 0) or 0)),
            # form key "speed_dial" → C++ field "speed_dial_number"
            "speed_dial_number": btn.get("speed_dial", ""),
            # bool fields: form sends "on"/"" — C++ optional<bool> via .number()
            "float":             1 if btn.get("show_in_floating_area") else 0,
            "ring":              1 if btn.get("audio_ring") else 0,
            "privacy":           1 if btn.get("default_private") else 0,
            "ringtone":          btn.get("ringtone", "") if btn.get("ringtone", "") != "default" else "",
            "forward_condition": btn.get("forward_condition", "always"),
            "button_colour":     btn.get("button_colour", ""),
        })
    return result


async def _safe_button_search(username: str) -> list:
    try:
        return await atp_client.button_search(username)
    except atp_client.AtpBackendError:
        return []


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _safe_user_search(q: str = "") -> list:
    try:
        users = await atp_client.user_search(q)
        return [u for u in users if u.get("role", "").lower() != "api"]
    except atp_client.AtpBackendError:
        return []


async def _safe_user_get(username: str) -> Optional[dict]:
    try:
        users = await atp_client.user_search(username)
        exact = [u for u in users if u.get("username") == username]
        return exact[0] if exact else None
    except atp_client.AtpBackendError:
        return None


async def _safe_group_search() -> list:
    try:
        groups_dict = await atp_client.group_search()
        return [
            {"id": data.get("id", name), "name": data.get("name", name)}
            for name, data in groups_dict.items()
        ]
    except atp_client.AtpBackendError:
        return []


def _validate_user_form(data: dict, is_add: bool) -> dict:
    errors: dict = {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    turret_pin = data.get("turret_pin", "")
    role = data.get("role", "User")

    if is_add:
        if not username:
            errors["username"] = "Username is required."
        elif len(username) < 3:
            errors["username"] = "Username must be at least 3 characters."
        elif not all(c.isalnum() or c in ".-" for c in username):
            errors["username"] = "Only A-Z, a-z, 0-9, . and - are allowed."
        if not password:
            errors["password"] = "Password is required."
    else:
        pass  # password is optional on edit

    if role == "User" and turret_pin:
        if not turret_pin.isdigit() or len(turret_pin) != 4:
            errors["turret_pin"] = "Access PIN must be exactly 4 digits."

    confirm = data.get("confirm_password", "")
    if password and confirm and password != confirm:
        errors["confirm_password"] = "Passwords do not match."

    return errors


def _form_to_payload(form, extra: dict | None = None) -> dict:
    """Convert raw request form to ATP backend payload."""
    payload = {
        "username":           (extra or {}).get("username") or form.get("username", ""),
        "password":           form.get("password", ""),
        "first_name":         form.get("first_name", ""),
        "last_name":          form.get("last_name", ""),
        "role":               form.get("role", "User"),
        "email":              form.get("email", ""),
        "turret_login":       form.get("turret_login", ""),
        "turret_pin":         form.get("turret_pin", ""),
        "timezone":           form.get("timezone", ""),
        "display_name":       form.get("display_name", ""),
        "device_type":        form.get("device_type", "fuse"),
        "group_ids":          form.getlist("group_ids"),
        "external_dialpad":   bool(form.get("external_dialpad")),
        "dialpad_number":     form.get("dialpad_number", ""),
        "group_latch":        bool(form.get("group_latch")),
        "latch_group_id":     form.get("latch_group_id", ""),
        "vr_retention_period": "{}_{}_{}_0_0".format(
            form.get("vr_retention_years") or form.get("retention_years", "0"),
            form.get("vr_retention_months") or form.get("retention_months", "0"),
            form.get("vr_retention_days") or form.get("retention_days", "0"),
        ),
    }
    return payload


async def _save_page_labels(form, turret_login: str) -> None:
    """Persist page labels via controller_pages_update (not controller_settings_update)."""
    pages = []
    for i in range(1, 58):
        name = (form.get(f"page_label_{i}") or "").strip()
        if name:
            pages.append({"name": name, "number": _PAGE_NUMBER_START + (i - 1)})
    if pages:
        try:
            await atp_client.pages_update(turret_login, pages)
        except atp_client.AtpBackendError as exc:
            logger.error("Page labels update failed for %r: %s", turret_login, exc)


def _form_to_settings(form) -> dict:
    """Extract turret settings from the form for controller_settings_update."""
    def _bool_val(key: str) -> str:
        return "1" if form.get(key) else "0"

    return {
        "home_page":                   form.get("home_page", "1"),
        "home_page_return_timeout":    form.get("home_page_return_timeout", "0"),
        "ringer_volume":               form.get("ringer_volume", "50"),
        "ringer_on_off":               _bool_val("ringer_on_off"),
        "handsfree_transmit_volume":   form.get("handsfree_transmit_volume", "50"),
        "left_transmit_broadcast_vol": form.get("left_transmit_broadcast_vol", "50"),
        "left_receive_broadcast_vol":  form.get("left_receive_broadcast_vol", "50"),
        "button_label_font_size":      form.get("button_label_font_size", "16"),
        "button_cli_font_size":        form.get("button_cli_font_size", "16"),
        "local_hunt_group":            form.get("local_hunt_group", "1"),
        "primary_timezone":            form.get("primary_timezone", ""),
        "auto_toggle_options":         form.get("auto_toggle_options", "none"),
        "display_sleep_time":          form.get("display_sleep_time", "1h"),
        "display_inactivity_timeout":  form.get("display_inactivity_timeout", "5min"),
        "display_wakeup_time":         form.get("display_wakeup_time", "6:00"),
        "ptt_move_to_hset":            _bool_val("ptt_move_to_hset"),
        "group_all_as_unlatch_all":    _bool_val("group_all_as_unlatch_all"),
        "right_handset_default":       _bool_val("right_handset_default"),
        "auto_show_call_control":      _bool_val("auto_show_call_control"),
        "noise_suppression_level":     form.get("noise_suppression_level", "off"),
        "default_handset_timeout":     form.get("default_handset_timeout", "30"),
        "display_recordings_on_turret": form.get("display_recordings_on_turret", "0"),
        "ext_dial_pad_as_std_tele_pad":    _bool_val("ext_dial_pad_as_std_tele_pad"),
        "normal_dial_pad_as_std_tele_pad": _bool_val("normal_dial_pad_as_std_tele_pad"),
        "group_all_device": form.get("group_all_device", "0"),
        "group_1_device":   form.get("group_1_device", "0"),
        "group_2_device":   form.get("group_2_device", "0"),
        "group_3_device":   form.get("group_3_device", "0"),
        "group_4_device":   form.get("group_4_device", "0"),
        "group_5_device":   form.get("group_5_device", "0"),
        "group_6_device":   form.get("group_6_device", "0"),
        "vr_retention_period": "{}_{}_{}_0_0".format(
            form.get("vr_retention_years") or form.get("retention_years", "0"),
            form.get("vr_retention_months") or form.get("retention_months", "0"),
            form.get("vr_retention_days") or form.get("retention_days", "0"),
        ),
    }


# ── Per-user advanced endpoints ───────────────────────────────────────────────

@router.post("/{username}/import-phone-numbers", response_class=JSONResponse)
async def users_import_phone_numbers(
    username: str,
    request: Request,
    session: dict = Depends(require_session),
):
    """
    Import Outlook contacts for a user.
    Accepts JSON: [{label, phone, company}] — each entry creates a contact in ATP.
    """
    contacts = await request.json()
    if not isinstance(contacts, list):
        return JSONResponse({"error": "Expected a JSON array of contacts."}, status_code=400)
    imported, errors = 0, []
    for c in contacts:
        phone = c.get("phone", "").strip()
        if not phone:
            continue
        try:
            await atp_client.user_add_contact(
                username,
                label=c.get("label", "").strip(),
                phone=phone,
                company=c.get("company", "").strip(),
            )
            imported += 1
        except atp_client.AtpBackendError as exc:
            errors.append({"label": c.get("label", ""), "error": str(exc)})
    logger.info("Phone numbers import: user=%r imported=%d errors=%d by=%r",
                username, imported, len(errors), session.get("username"))
    return {"imported": imported, "errors": errors}


@router.post("/{username}/reset-buttons", response_class=JSONResponse)
async def users_reset_buttons(
    username: str,
    request: Request,
    session: dict = Depends(require_session),
):
    """
    Delete all buttons in a position range for a user.
    Body: {start_pos, end_pos}  (0-based inclusive)
    """
    body      = await request.json()
    start_pos = int(body.get("start_pos", 1))
    end_pos   = int(body.get("end_pos",   56))
    positions = list(range(start_pos, end_pos + 1))
    try:
        await atp_client.button_delete_positions(username, positions)
        logger.info("Reset buttons: user=%r range=%d-%d by=%r", username, start_pos, end_pos, session.get("username"))
        return {"success": True}
    except atp_client.AtpBackendError as exc:
        return {"success": False, "message": str(exc)}


@router.post("/{username}/speaker-settings", response_class=JSONResponse)
async def users_speaker_settings(
    username: str,
    request: Request,
    session: dict = Depends(require_session),
):
    """Save the 24-channel speaker setting for a user. Body: {value: "24"}"""
    body  = await request.json()
    value = str(body.get("value", "")).strip()
    if not value:
        return JSONResponse({"error": "value is required."}, status_code=400)
    try:
        await atp_client.user_speaker_setting(username, value)
        logger.info("Speaker setting: user=%r value=%r by=%r", username, value, session.get("username"))
        return {"success": True}
    except atp_client.AtpBackendError as exc:
        return {"success": False, "message": str(exc)}
