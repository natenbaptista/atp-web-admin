"""
routers/groups.py — /groups/*

Ports: atp-dev/src/manager/app/Controller/UsergroupsController.php

Routes:
  GET  /groups                               → group list (paginated)
  GET  /groups/add                           → add form
  POST /groups/add                           → create group
  GET  /groups/{name}/edit                   → edit form
  POST /groups/{name}/edit                   → update group
  POST /groups/{name}/delete                 → delete group
  GET  /groups/{name}/directory              → directory entries (paginated)
  POST /groups/{name}/directory/add          → add directory entry
  GET  /groups/{name}/directory/{id}/json    → get single entry as JSON
  GET  /groups/{name}/directory/{id}/edit    → edit form
  POST /groups/{name}/directory/{id}/edit    → update directory entry
  POST /groups/{name}/directory/{id}/delete  → delete directory entry
  GET  /groups/custom/{username}             → custom groups for user
  POST /groups/custom/{username}             → add/edit/delete custom group
"""

import pathlib
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

import atp_client
from session import require_session, wants_json
from logging_config import logger

router = APIRouter(prefix="/groups")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"

_PER_PAGE_GROUPS = 10
_PER_PAGE_DIRS   = 15


# ── Index ─────────────────────────────────────────────────────────────────────

@router.get("")
async def groups_index():
    return FileResponse(PUBLIC_DIR / "index.html")


# ── AJAX search (JSON) ────────────────────────────────────────────────────────

@router.get("/search", response_class=JSONResponse)
async def groups_search_json(
    q: str = "",
    session: dict = Depends(require_session),
):
    """Return groups as JSON list; group_search() returns a dict {name: data}."""
    raw = await _safe_group_search()
    result = []
    for name, data in raw.items():
        if q and q.lower() not in name.lower():
            continue
        if isinstance(data, dict):
            users = data.get("users", [])
        elif isinstance(data, list):
            users = data
        else:
            users = []
        result.append({"name": name, "user_count": len(users), "users": users})
    return result


@router.get("/{name}/json", response_class=JSONResponse)
async def groups_detail_json(
    name: str,
    session: dict = Depends(require_session),
):
    """Return single group details as JSON."""
    data = await _safe_group_get(name)
    if isinstance(data, dict):
        users = data.get("users", [])
    elif isinstance(data, list):
        users = data
    else:
        users = []
    return {"name": name, "users": users}


@router.get("/custom/{username}/json", response_class=JSONResponse)
async def groups_custom_json(
    username: str,
    session: dict = Depends(require_session),
):
    """Return custom groups for a user as JSON."""
    custom_groups, all_usernames = await _safe_custom_groups(username)
    # custom_groups may be a dict {group_name: [usernames]} or similar
    result = []
    if isinstance(custom_groups, dict):
        for gname, members in custom_groups.items():
            result.append({
                "name": gname,
                "members": members if isinstance(members, list) else [],
            })
    return {"groups": result, "all_usernames": all_usernames}


# ── Add ───────────────────────────────────────────────────────────────────────

@router.get("/add")
async def groups_add_get():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/add")
async def groups_add_post(
    request: Request,
    session: dict = Depends(require_session),
):
    form     = await request.form()
    name     = (form.get("name") or "").strip()
    users    = form.getlist("users")
    errors   = []

    if not name:
        errors.append("Group name is required.")
    elif not _valid_group_name(name):
        errors.append("Group name may only contain letters, digits, - and .")

    if errors:
        if wants_json(request):
            return JSONResponse({"errors": {"__all": errors}}, status_code=422)
        return RedirectResponse(url=request.url.path, status_code=303)

    try:
        await atp_client.group_create({"name": name, "type": "normal", "users": users})
        logger.info("Group created: %r by %r", name, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Group create failed: %r: %s", name, exc)
        if wants_json(request):
            return JSONResponse({"errors": {"__global": str(exc)}}, status_code=502)
        return RedirectResponse(url=request.url.path, status_code=303)

    return RedirectResponse(url="/groups", status_code=303)


# ── Edit ──────────────────────────────────────────────────────────────────────

@router.get("/{name}/edit")
async def groups_edit_get():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/{name}/edit")
async def groups_edit_post(
    name: str,
    request: Request,
    session: dict = Depends(require_session),
):
    form   = await request.form()
    users  = form.getlist("users")
    try:
        await atp_client.group_update(name, {"users": users})
        logger.info("Group updated: %r by %r", name, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Group update failed: %r: %s", name, exc)
        if wants_json(request):
            return JSONResponse({"errors": {"__global": str(exc)}}, status_code=502)
        return RedirectResponse(url=request.url.path, status_code=303)
    return RedirectResponse(url="/groups", status_code=303)


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/{name}/delete")
async def groups_delete(
    name: str,
    session: dict = Depends(require_session),
):
    try:
        await atp_client.group_delete(name)
        logger.info("Group deleted: %r by %r", name, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Group delete failed: %r: %s", name, exc)
    return RedirectResponse(url="/groups", status_code=303)


# ── Directory ─────────────────────────────────────────────────────────────────

@router.get("/{group_name}/directory")
async def groups_directory():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/{group_name}/directory/add")
async def groups_directory_add(
    group_name: str,
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    name          = (form.get("name") or "").strip()
    description   = (form.get("description") or "").strip()
    number_office = (form.get("number_office") or "").strip()
    number_mob    = (form.get("number_mob") or "").strip()
    number_home   = (form.get("number_home") or "").strip()

    if not name:
        return RedirectResponse(url=f"/groups/{group_name}/directory", status_code=303)

    contact_number = f"{number_office}|{number_mob}|{number_home}"
    try:
        await atp_client.group_dir_create(group_name, {
            "name":           name,
            "description":    description,
            "contact_number": contact_number,
        })
        logger.info("Dir entry added: group=%r name=%r by %r",
                    group_name, name, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Dir entry add failed: group=%r: %s", group_name, exc)

    return RedirectResponse(url=f"/groups/{group_name}/directory", status_code=303)


@router.get("/{group_name}/directory/{entry_id}/json", response_class=JSONResponse)
async def groups_directory_entry_json(
    group_name: str,
    entry_id: str,
    session: dict = Depends(require_session),
):
    """Return a single directory entry by id (for the edit form)."""
    try:
        entries = await atp_client.group_dir_search(group_name)
        for e in entries:
            if str(e.get("id", e.get("row_id", ""))) == str(entry_id):
                parts = str(e.get("contact_number", "")).split("|")
                return {
                    "id":             entry_id,
                    "name":           e.get("name", e.get("contact_name", "")),
                    "description":    e.get("description", e.get("contact_description", "")),
                    "number_office":  parts[0] if len(parts) > 0 else "",
                    "number_mob":     parts[1] if len(parts) > 1 else "",
                    "number_home":    parts[2] if len(parts) > 2 else "",
                }
        return JSONResponse({"error": "Entry not found"}, status_code=404)
    except atp_client.AtpBackendError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@router.get("/{group_name}/directory/{entry_id}/edit")
async def groups_directory_edit_get():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/{group_name}/directory/{entry_id}/edit")
async def groups_directory_edit_post(
    group_name: str,
    entry_id: str,
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    name          = (form.get("name") or "").strip()
    description   = (form.get("description") or "").strip()
    number_office = (form.get("number_office") or "").strip()
    number_mob    = (form.get("number_mob") or "").strip()
    number_home   = (form.get("number_home") or "").strip()

    if not name:
        return RedirectResponse(url=f"/groups/{group_name}/directory/{entry_id}/edit", status_code=303)

    contact_number = f"{number_office}|{number_mob}|{number_home}"
    try:
        await atp_client.group_dir_update(group_name, entry_id, {
            "name":           name,
            "description":    description,
            "contact_number": contact_number,
        })
        logger.info("Dir entry updated: group=%r id=%r by %r",
                    group_name, entry_id, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Dir entry update failed: group=%r id=%r: %s", group_name, entry_id, exc)

    return RedirectResponse(url=f"/groups/{group_name}/directory", status_code=303)


@router.post("/{group_name}/directory/{entry_id}/delete")
async def groups_directory_delete(
    group_name: str,
    entry_id: str,
    session: dict = Depends(require_session),
):
    try:
        await atp_client.group_dir_delete(group_name, entry_id)
        logger.info("Dir entry deleted: group=%r id=%r by %r",
                    group_name, entry_id, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Dir entry delete failed: group=%r id=%r: %s", group_name, entry_id, exc)
    return RedirectResponse(url=f"/groups/{group_name}/directory", status_code=303)


# ── Custom groups (per-user) ──────────────────────────────────────────────────

@router.get("/custom/{username}")
async def groups_custom_get():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/custom/{username}")
async def groups_custom_post(
    username: str,
    request: Request,
    session: dict = Depends(require_session),
):
    form          = await request.form()
    action        = (form.get("action") or "add").strip()
    custom_group  = (form.get("custom_group") or "").strip()
    user_name_raw = form.getlist("user_name") or (form.get("user_name") or "").split(",")
    users_added   = [u.strip() for u in user_name_raw if u.strip()]

    if username not in users_added:
        users_added.append(username)

    try:
        await atp_client.custom_group_action(username, action, custom_group, users_added)
        logger.info("Custom group %s: group=%r by %r", action, custom_group, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Custom group %s failed: group=%r: %s", action, custom_group, exc)

    return RedirectResponse(url=f"/groups/custom/{username}", status_code=303)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _valid_group_name(name: str) -> bool:
    import re
    return bool(re.match(r'^[A-Za-z0-9.\-]+$', name))


async def _safe_group_search() -> dict:
    try:
        return await atp_client.group_search()
    except atp_client.AtpBackendError as exc:
        logger.error("Group search failed: %s", exc)
        return {}


async def _safe_group_get(name: str) -> dict:
    try:
        return await atp_client.group_get(name)
    except atp_client.AtpBackendError as exc:
        logger.error("Group get failed: %r: %s", name, exc)
        return {"name": name, "users": []}


async def _safe_user_list() -> list:
    try:
        raw = await atp_client.user_search("")
        return [u.get("username", u) if isinstance(u, dict) else u for u in raw
                if (u.get("username") if isinstance(u, dict) else u) != "root"]
    except atp_client.AtpBackendError:
        return []


async def _safe_dir_list(group_name: str) -> list:
    try:
        return await atp_client.group_dir_search(group_name)
    except atp_client.AtpBackendError as exc:
        logger.error("Dir list failed: group=%r: %s", group_name, exc)
        return []


async def _safe_custom_groups(username: str) -> tuple[dict, list]:
    try:
        groups    = await atp_client.custom_group_search(username)
        all_users = await atp_client.user_search("")
        unames    = [u.get("username", u) if isinstance(u, dict) else u for u in all_users
                     if (u.get("username") if isinstance(u, dict) else u) not in (username, "root")]
        return groups, unames
    except atp_client.AtpBackendError as exc:
        logger.error("Custom groups failed: user=%r: %s", username, exc)
        return {}, []
