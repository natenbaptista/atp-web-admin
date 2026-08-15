"""
routers/outbounds.py — /outbounds/*

Ports: atp-dev/src/manager/app/Controller/OutboundsController.php

Routes:
  GET  /outbounds                → outbound rules list
  GET  /outbounds/add            → add form
  POST /outbounds/add            → create outbound rule
  GET  /outbounds/{name}/edit    → edit form
  POST /outbounds/{name}/edit    → update outbound rule
  POST /outbounds/{name}/delete  → delete outbound rule
"""

import pathlib

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

import atp_client
from session import require_session, wants_json
from logging_config import logger

router = APIRouter(prefix="/outbounds")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"


# ── Index ─────────────────────────────────────────────────────────────────────

@router.get("")
async def outbounds_index():
    return FileResponse(PUBLIC_DIR / "index.html")


# ── AJAX search (JSON) ────────────────────────────────────────────────────────

@router.get("/search", response_class=JSONResponse)
async def outbounds_search_json(
    q: str = "",
    session: dict = Depends(require_session),
):
    raw = await _safe_outbound_search()
    if q:
        q_lower = q.lower()
        raw = [r for r in raw if q_lower in str(r.get("name", "")).lower()]
    return raw


# ── Add ───────────────────────────────────────────────────────────────────────

@router.get("/add")
async def outbounds_add_get():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/add")
async def outbounds_add_post(
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    payload = _form_to_payload(form)
    errors = _validate(payload, is_add=True)

    if errors:
        if wants_json(request):
            return JSONResponse({"errors": {"__all": errors}}, status_code=422)
        return RedirectResponse(url=request.url.path, status_code=303)

    try:
        await atp_client.outbound_create(payload)
        logger.info("Outbound created: %r by %r", payload.get("name"), session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Outbound create failed: %s", exc)
        if wants_json(request):
            return JSONResponse({"errors": {"__global": str(exc)}}, status_code=502)
        return RedirectResponse(url=request.url.path, status_code=303)

    return RedirectResponse(url="/outbounds", status_code=303)


# ── Edit ──────────────────────────────────────────────────────────────────────

@router.get("/{name}/edit")
async def outbounds_edit_get():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/{name}/edit")
async def outbounds_edit_post(
    name: str,
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    payload = _form_to_payload(form)
    payload["name"] = name  # name readonly on edit
    errors = _validate(payload, is_add=False)

    if errors:
        if wants_json(request):
            return JSONResponse({"errors": {"__all": errors}}, status_code=422)
        return RedirectResponse(url=request.url.path, status_code=303)

    try:
        await atp_client.outbound_update(name, payload)
        logger.info("Outbound updated: %r by %r", name, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Outbound update failed: %r: %s", name, exc)
        if wants_json(request):
            return JSONResponse({"errors": {"__global": str(exc)}}, status_code=502)
        return RedirectResponse(url=request.url.path, status_code=303)

    return RedirectResponse(url="/outbounds", status_code=303)


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/{name}/delete")
async def outbounds_delete(
    name: str,
    session: dict = Depends(require_session),
):
    try:
        await atp_client.outbound_delete(name)
        logger.info("Outbound deleted: %r by %r", name, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Outbound delete failed: %r: %s", name, exc)
    return RedirectResponse(url="/outbounds", status_code=303)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _form_to_payload(form) -> dict:
    return {
        "name":              form.get("name", "").strip(),
        "dial_plan_match":   form.get("dial_plan_match", "").strip(),
        "translation_match": form.get("translation_match", "").strip(),
        "translation_sub":   form.get("translation_sub", "").strip(),
        "route":             (form.getlist("routes") or [form.get("route", "")])[0].strip(),
    }


def _validate(payload: dict, is_add: bool) -> list:
    errors = []
    if is_add and not payload.get("name"):
        errors.append("Name is required.")
    if not payload.get("dial_plan_match"):
        errors.append("Dial Plan Match is required.")
    return errors


async def _safe_outbound_search() -> list:
    try:
        return await atp_client.outbound_search()
    except atp_client.AtpBackendError as exc:
        logger.error("Outbound search failed: %s", exc)
        return []


async def _safe_outbound_get(name: str) -> dict:
    try:
        return await atp_client.outbound_get(name)
    except atp_client.AtpBackendError as exc:
        logger.error("Outbound get failed: %r: %s", name, exc)
        return {"name": name}


async def _safe_route_list() -> list:
    try:
        raw = await atp_client.route_search()
        return [r.get("name", r) if isinstance(r, dict) else r for r in raw]
    except atp_client.AtpBackendError:
        return []
