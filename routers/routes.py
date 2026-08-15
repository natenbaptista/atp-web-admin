"""
routers/routes.py — /routes/*

Ports: atp-dev/src/manager/app/Controller/RoutesController.php

Routes:
  GET  /routes               → route list
  GET  /routes/add           → add form
  POST /routes/add           → create route
  GET  /routes/{name}/edit   → edit form
  POST /routes/{name}/edit   → update route
  POST /routes/{name}/delete → delete route
"""

import pathlib

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

import atp_client
from session import require_session, wants_json
from logging_config import logger

router = APIRouter(prefix="/routes")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"


# ── Index ─────────────────────────────────────────────────────────────────────

@router.get("")
async def routes_index():
    return FileResponse(PUBLIC_DIR / "index.html")


# ── AJAX search (JSON) ────────────────────────────────────────────────────────

@router.get("/search", response_class=JSONResponse)
async def routes_search_json(
    q: str = "",
    session: dict = Depends(require_session),
):
    raw = await _safe_route_search()
    if q:
        q_lower = q.lower()
        raw = [r for r in raw if q_lower in str(r.get("route", r.get("name", ""))).lower()]
    return raw


# ── Add ───────────────────────────────────────────────────────────────────────

@router.get("/add")
async def routes_add_get():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/add")
async def routes_add_post(
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
        await atp_client.route_create(payload)
        logger.info("Route created: %r by %r", payload.get("route"), session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Route create failed: %s", exc)
        if wants_json(request):
            return JSONResponse({"errors": {"__global": str(exc)}}, status_code=502)
        return RedirectResponse(url=request.url.path, status_code=303)

    return RedirectResponse(url="/routes", status_code=303)


# ── Single route detail (for edit form pre-fill) ─────────────────────────────

@router.get("/{name}/detail", response_class=JSONResponse)
async def routes_detail(
    name: str,
    session: dict = Depends(require_session),
):
    """Return a single route by exact name — used by the edit form."""
    routes = await _safe_route_search()
    route = next(
        (r for r in routes if r.get("route") == name or r.get("name") == name),
        None,
    )
    if not route:
        return JSONResponse({"error": "Route not found"}, status_code=404)
    # Normalise: always expose both 'route' and 'trunks' keys
    return {
        "route":  route.get("route") or route.get("name") or name,
        "trunks": route.get("trunks") or [],
    }


# ── Edit ──────────────────────────────────────────────────────────────────────

@router.get("/{name}/edit")
async def routes_edit_get():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/{name}/edit")
async def routes_edit_post(
    name: str,
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    payload = _form_to_payload(form)
    payload["route"] = name  # name is readonly on edit
    errors = _validate(payload, is_add=False)

    if errors:
        if wants_json(request):
            return JSONResponse({"errors": {"__all": errors}}, status_code=422)
        return RedirectResponse(url=request.url.path, status_code=303)

    try:
        await atp_client.route_update(name, payload)
        logger.info("Route updated: %r by %r", name, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Route update failed: %r: %s", name, exc)
        if wants_json(request):
            return JSONResponse({"errors": {"__global": str(exc)}}, status_code=502)
        return RedirectResponse(url=request.url.path, status_code=303)

    return RedirectResponse(url="/routes", status_code=303)


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/{name}/delete")
async def routes_delete(
    name: str,
    session: dict = Depends(require_session),
):
    try:
        await atp_client.route_delete(name)
        logger.info("Route deleted: %r by %r", name, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Route delete failed: %r: %s", name, exc)
    return RedirectResponse(url="/routes", status_code=303)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _form_to_payload(form) -> dict:
    # C++ Route class only has two fields: "route" (name) and "trunks" (list of trunk names).
    # trunks[0] = primary, trunks[1] = failover 1, trunks[2] = failover 2 (PHP behaviour).
    trunks = []
    for key in ("trunk", "failover_trunk", "failover_trunk2"):
        val = (form.get(key) or "").strip()
        if val:
            trunks.append(val)
    return {
        "route":  form.get("name", "").strip(),
        "trunks": trunks,
    }


def _validate(payload: dict, is_add: bool) -> list:
    errors = []
    if is_add and not payload.get("route"):
        errors.append("Name is required.")
    if not payload.get("trunks"):
        errors.append("A trunk must be selected.")
    return errors


async def _safe_route_search() -> list:
    try:
        return await atp_client.route_search()
    except atp_client.AtpBackendError as exc:
        logger.error("Route search failed: %s", exc)
        return []


async def _safe_route_get(name: str) -> dict:
    try:
        routes = await atp_client.route_search()
        for r in routes:
            if r.get("name") == name:
                return r
        return {"name": name}
    except atp_client.AtpBackendError as exc:
        logger.error("Route get failed: %r: %s", name, exc)
        return {"name": name}


async def _safe_trunk_list() -> list:
    try:
        raw = await atp_client.trunk_search()
        return [t.get("name", t) if isinstance(t, dict) else t for t in raw]
    except atp_client.AtpBackendError:
        return []
