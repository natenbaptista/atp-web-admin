"""
routers/inbounds.py — /inbounds/*

Ports: atp-dev/src/manager/app/Controller/InboundsController.php

Routes:
  GET  /inbounds                 → inbound rules list
  GET  /inbounds/add             → add form
  POST /inbounds/add             → create inbound rule
  GET  /inbounds/{name}/edit     → edit form
  POST /inbounds/{name}/edit     → update inbound rule
  POST /inbounds/{name}/delete   → delete inbound rule
"""

import pathlib

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

import atp_client
from session import require_session, wants_json
from logging_config import logger

router = APIRouter(prefix="/inbounds")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"


# ── Index ─────────────────────────────────────────────────────────────────────

@router.get("")
async def inbounds_index():
    return FileResponse(PUBLIC_DIR / "index.html")


# ── AJAX search (JSON) ────────────────────────────────────────────────────────

@router.get("/search", response_class=JSONResponse)
async def inbounds_search_json(
    q: str = "",
    session: dict = Depends(require_session),
):
    raw = await _safe_inbound_search()
    if q:
        q_lower = q.lower()
        raw = [r for r in raw if q_lower in str(r.get("name", "")).lower()]
    return raw


# ── Add ───────────────────────────────────────────────────────────────────────

@router.get("/add")
async def inbounds_add_get():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/add")
async def inbounds_add_post(
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
        await atp_client.inbound_create(payload)
        logger.info("Inbound created: %r by %r", payload.get("name"), session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Inbound create failed: %s", exc)
        if wants_json(request):
            return JSONResponse({"errors": {"__global": str(exc)}}, status_code=502)
        return RedirectResponse(url=request.url.path, status_code=303)

    return RedirectResponse(url="/inbounds", status_code=303)


# ── Edit ──────────────────────────────────────────────────────────────────────

@router.get("/{name}/edit")
async def inbounds_edit_get():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/{name}/edit")
async def inbounds_edit_post(
    name: str,
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    payload = _form_to_payload(form)
    payload["name"] = name  # name is readonly on edit
    errors = _validate(payload, is_add=False)

    if errors:
        if wants_json(request):
            return JSONResponse({"errors": {"__all": errors}}, status_code=422)
        return RedirectResponse(url=request.url.path, status_code=303)

    try:
        await atp_client.inbound_update(name, payload)
        logger.info("Inbound updated: %r by %r", name, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Inbound update failed: %r: %s", name, exc)
        if wants_json(request):
            return JSONResponse({"errors": {"__global": str(exc)}}, status_code=502)
        return RedirectResponse(url=request.url.path, status_code=303)

    return RedirectResponse(url="/inbounds", status_code=303)


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/{name}/delete")
async def inbounds_delete(
    name: str,
    session: dict = Depends(require_session),
):
    try:
        await atp_client.inbound_delete(name)
        logger.info("Inbound deleted: %r by %r", name, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Inbound delete failed: %r: %s", name, exc)
    return RedirectResponse(url="/inbounds", status_code=303)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _form_to_payload(form) -> dict:
    return {
        "name":                    form.get("name", "").strip(),
        "dial_plan_match":         form.get("dial_plan_match", "").strip(),
        "trunk":                   form.get("trunk", "").strip(),
        # DNI-to-DP translation
        "dni_to_dp_match":         form.get("dni_to_dp_match", "").strip(),
        "dni_to_dp_sub":           form.get("dni_to_dp_sub", "").strip(),
        "dni_to_dp_strip_front":   int(form.get("dni_to_dp_strip_front", 0) or 0),
        "dni_to_dp_strip_end":     int(form.get("dni_to_dp_strip_end", 0) or 0),
        "dni_to_dp_prefix":        form.get("dni_to_dp_prefix", "").strip(),
        "dni_to_dp_postfix":       form.get("dni_to_dp_postfix", "").strip(),
        # Source number translation
        "source_number_match":        form.get("source_number_match", "").strip(),
        "source_number_sub":          form.get("source_number_sub", "").strip(),
        "source_number_strip_front":  int(form.get("source_number_strip_front", 0) or 0),
        "source_number_strip_end":    int(form.get("source_number_strip_end", 0) or 0),
        "source_number_prefix":       form.get("source_number_prefix", "").strip(),
        "source_number_postfix":      form.get("source_number_postfix", "").strip(),
    }


def _validate(payload: dict, is_add: bool) -> list:
    errors = []
    if is_add and not payload.get("name"):
        errors.append("Name is required.")
    if not payload.get("dial_plan_match"):
        errors.append("Dial Plan Match is required.")
    return errors


async def _autocomplete_lists() -> tuple[list, list]:
    trunks, lines = [], []
    try:
        raw = await atp_client.trunk_search()
        trunks = [t.get("name", t) if isinstance(t, dict) else t for t in raw]
    except atp_client.AtpBackendError:
        pass
    try:
        raw = await atp_client.line_search("")
        lines = [l.get("name") or l.get("dn", "") for l in raw if l.get("name") or l.get("dn")]
    except atp_client.AtpBackendError:
        pass
    return trunks, lines


async def _safe_inbound_search() -> list:
    try:
        return await atp_client.inbound_search()
    except atp_client.AtpBackendError as exc:
        logger.error("Inbound search failed: %s", exc)
        return []


async def _safe_inbound_get(name: str) -> dict:
    try:
        return await atp_client.inbound_get(name)
    except atp_client.AtpBackendError as exc:
        logger.error("Inbound get failed: %r: %s", name, exc)
        return {"name": name}
