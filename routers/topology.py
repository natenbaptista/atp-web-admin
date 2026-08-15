"""
routers/topology.py — /topology/*

Ports: atp-dev/src/manager/app/Controller/TopologyController.php

Routes:
  GET  /topology                    → site list with actors
  POST /topology                    → create new site
  GET  /topology/{site}/edit        → edit site (move actors form)
  POST /topology/{site}/edit        → move actors to another site
  POST /topology/{site}/delete      → delete site
"""

import pathlib

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

import atp_client
from session import require_session
from logging_config import logger

router = APIRouter(prefix="/topology")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"


# ── Index ─────────────────────────────────────────────────────────────────────

@router.get("")
async def topology_index():
    return FileResponse(PUBLIC_DIR / "index.html")


# ── JSON: sites list ──────────────────────────────────────────────────────────

@router.get("/sites", response_class=JSONResponse)
async def topology_sites_json(
    session: dict = Depends(require_session),
):
    """Return all sites with their actors as a JSON list.
    site_search() returns list[str] of site names; fetch actors per site."""
    site_names = await _safe_site_names()
    result = []
    for name in site_names:
        actors = await _safe_actors_for_site(name)
        # actors are NodeDN dicts: {CN, role, O} — extract CN strings for display
        actor_cns = [a.get("CN", a.get("cn", "")) for a in actors if isinstance(a, dict)]
        result.append({"id": name, "name": name, "actors": actor_cns})
    return result


@router.post("")
async def topology_create_site(
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    site_name = (form.get("site_name") or "").strip()
    if not site_name:
        return RedirectResponse(url=request.url.path, status_code=303)
    try:
        await atp_client.site_create(site_name)
        logger.info("Site created: %r by %r", site_name, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Site create failed: %r: %s", site_name, exc)
        return RedirectResponse(url=request.url.path, status_code=303)
    return RedirectResponse(url="/topology", status_code=303)


# ── Edit (move actors) ────────────────────────────────────────────────────────

@router.get("/{site}/edit")
async def topology_edit_get():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/{site}/edit")
async def topology_edit_post(
    site: str,
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    target      = (form.get("move_target") or "").strip()
    move_cns    = list(form.getlist("move_actors"))

    if target and move_cns:
        try:
            # Fetch full actor info (CN, role, O) so the backend can identify them properly
            all_actors  = await _safe_actors_for_site(site)
            actor_map   = {
                a.get("CN", a.get("cn", "")): a
                for a in all_actors if isinstance(a, dict)
            }
            full_actors = [
                actor_map.get(cn, {"CN": cn, "role": "", "O": ""})
                for cn in move_cns
            ]
            await atp_client.site_move_actors(site, target, full_actors)
            logger.info("Actors moved: from=%r to=%r actors=%r by %r",
                        site, target, move_cns, session.get("username"))
        except atp_client.AtpBackendError as exc:
            logger.error("Site move actors failed: %s", exc)
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    return JSONResponse({"ok": True})


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/{site}/delete")
async def topology_delete(
    site: str,
    session: dict = Depends(require_session),
):
    try:
        await atp_client.site_delete(site)
        logger.info("Site deleted: %r by %r", site, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Site delete failed: %r: %s", site, exc)
    return RedirectResponse(url="/topology", status_code=303)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _safe_site_names() -> list[str]:
    try:
        return await atp_client.site_search()
    except atp_client.AtpBackendError as exc:
        logger.error("Site list failed: %s", exc)
        return []


async def _safe_actors_for_site(site: str) -> list[dict]:
    try:
        return await atp_client.site_actors(site)
    except atp_client.AtpBackendError as exc:
        logger.error("Site actors failed: %r: %s", site, exc)
        return []
