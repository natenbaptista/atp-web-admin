"""
routers/trunks.py — /trunks/*

Ports: atp-dev/src/manager/app/Controller/TrunksController.php

Routes:
  GET  /trunks                          → trunk list
  GET  /trunks/add                      → add form
  POST /trunks/add                      → create trunk
  GET  /trunks/{name}/edit              → edit form
  POST /trunks/{name}/edit              → update trunk
  POST /trunks/{name}/delete            → delete trunk
  GET  /trunks/{name}/sipreg            → SIP registration list
  GET  /trunks/{name}/sipreg/add        → add SIP reg form
  POST /trunks/{name}/sipreg/add        → create SIP reg
  GET  /trunks/{name}/sipreg/{reg_id}/edit   → edit SIP reg form
  POST /trunks/{name}/sipreg/{reg_id}/edit   → update SIP reg
  POST /trunks/{name}/sipreg/{reg_id}/delete → delete SIP reg
"""

import pathlib

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

import atp_client
from session import require_session, wants_json
from logging_config import logger

router = APIRouter(prefix="/trunks")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"

_MONITORING_METHODS = ["None", "OPTIONS"]  # "REGISTER" not supported by ATP backend
_SIP_PROTOCOLS      = ["UDP", "TCP"]        # "TLS" not supported by ATP backend

# Map form/UI values → C++ enum strings
_MONITORING_METHOD_MAP = {
    "None":        "None",
    "OPTIONS":     "Sip_Options",
    "Sip OPTIONS": "Sip_Options",  # alias in case old frontend sends it
}
_SIP_PROTOCOL_MAP = {
    "UDP": "UDP",
    "TCP": "TCP",
}


# ── Index ─────────────────────────────────────────────────────────────────────

@router.get("")
async def trunks_index():
    return FileResponse(PUBLIC_DIR / "index.html")


# ── AJAX search (JSON) ────────────────────────────────────────────────────────

@router.get("/search", response_class=JSONResponse)
async def trunks_search_json(
    q: str = "",
    session: dict = Depends(require_session),
):
    raw = await _safe_trunk_search()
    if q:
        q_lower = q.lower()
        raw = [t for t in raw if q_lower in str(t.get("name", "")).lower()]
    # Map C++ field "pstn" to frontend field "external_sip_address"
    for trunk in raw:
        if "pstn" in trunk and "external_sip_address" not in trunk:
            trunk["external_sip_address"] = trunk["pstn"]
    return raw


# ── Add ───────────────────────────────────────────────────────────────────────

@router.get("/add")
async def trunks_add_get():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/add")
async def trunks_add_post(
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    payload = _form_to_payload(form)
    errors = _validate_trunk(payload, is_add=True)

    if errors:
        if wants_json(request):
            return JSONResponse({"errors": {"__all": errors}}, status_code=422)
        return RedirectResponse(url=request.url.path, status_code=303)

    try:
        await atp_client.trunk_create(payload)
        logger.info("Trunk created: %r by %r", payload.get("name"), session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Trunk create failed: %s", exc)
        if wants_json(request):
            return JSONResponse({"errors": {"__global": str(exc)}}, status_code=502)
        return RedirectResponse(url=request.url.path, status_code=303)

    return RedirectResponse(url="/trunks", status_code=303)


# ── Edit ──────────────────────────────────────────────────────────────────────

@router.get("/{name}/edit")
async def trunks_edit_get():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/{name}/edit")
async def trunks_edit_post(
    name: str,
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    payload = _form_to_payload(form)
    payload["name"] = name  # name is readonly on edit
    errors = _validate_trunk(payload, is_add=False)

    if errors:
        if wants_json(request):
            return JSONResponse({"errors": {"__all": errors}}, status_code=422)
        return RedirectResponse(url=request.url.path, status_code=303)

    try:
        await atp_client.trunk_update(name, payload)
        logger.info("Trunk updated: %r by %r", name, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Trunk update failed: %r: %s", name, exc)
        if wants_json(request):
            return JSONResponse({"errors": {"__global": str(exc)}}, status_code=502)
        return RedirectResponse(url=request.url.path, status_code=303)

    return RedirectResponse(url="/trunks", status_code=303)


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/{name}/delete")
async def trunks_delete(
    name: str,
    session: dict = Depends(require_session),
):
    try:
        await atp_client.trunk_delete(name)
        logger.info("Trunk deleted: %r by %r", name, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("Trunk delete failed: %r: %s", name, exc)
    return RedirectResponse(url="/trunks", status_code=303)


# ── Gateways JSON ─────────────────────────────────────────────────────────────

@router.get("/gateways-json", response_class=JSONResponse)
async def trunks_gateways_json(
    session: dict = Depends(require_session),
):
    """Return all gateway names for the dual-listbox in the trunk form."""
    try:
        all_gateways = await atp_client.gateway_search()
        names = [g.get("name", g) if isinstance(g, dict) else str(g) for g in all_gateways]
    except atp_client.AtpBackendError as exc:
        logger.error("Gateway search failed: %s", exc)
        names = []
    return {"gateways": names}


# ── SIP Registrations ─────────────────────────────────────────────────────────

@router.get("/{trunk_name}/sipreg/list", response_class=JSONResponse)
async def trunks_sipreg_list(
    trunk_name: str,
    session: dict = Depends(require_session),
):
    """Return SIP registrations for a trunk as JSON."""
    regs = await _safe_sipreg_list(trunk_name)
    return regs


@router.get("/{trunk_name}/sipreg")
async def trunks_sipreg():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.get("/{trunk_name}/sipreg/add")
async def trunks_sipreg_add_get():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/{trunk_name}/sipreg/add")
async def trunks_sipreg_add_post(
    trunk_name: str,
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    payload = {
        "username": form.get("username", "").strip(),
        "password": form.get("password", ""),
        "registrar": form.get("registrar", "").strip(),
        "expiry":   int(form.get("expiry", 3600) or 3600),
    }
    try:
        await atp_client.sipreg_create(trunk_name, payload)
        logger.info("SIP reg added: trunk=%r user=%r by %r",
                    trunk_name, payload["username"], session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("SIP reg add failed: trunk=%r: %s", trunk_name, exc)
    return RedirectResponse(url=f"/trunks/{trunk_name}/sipreg", status_code=303)


@router.get("/{trunk_name}/sipreg/{reg_id}/edit")
async def trunks_sipreg_edit_get():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/{trunk_name}/sipreg/{reg_id}/edit")
async def trunks_sipreg_edit_post(
    trunk_name: str,
    reg_id: str,
    request: Request,
    session: dict = Depends(require_session),
):
    form = await request.form()
    payload = {
        "username": form.get("username", "").strip(),
        "registrar": form.get("registrar", "").strip(),
        "expiry":   int(form.get("expiry", 3600) or 3600),
    }
    pw = form.get("password", "")
    if pw:
        payload["password"] = pw
    try:
        await atp_client.sipreg_update(trunk_name, reg_id, payload)
        logger.info("SIP reg updated: trunk=%r id=%r by %r",
                    trunk_name, reg_id, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("SIP reg update failed: trunk=%r id=%r: %s", trunk_name, reg_id, exc)
    return RedirectResponse(url=f"/trunks/{trunk_name}/sipreg", status_code=303)


@router.post("/{trunk_name}/sipreg/{reg_id}/delete")
async def trunks_sipreg_delete(
    trunk_name: str,
    reg_id: str,
    session: dict = Depends(require_session),
):
    try:
        await atp_client.sipreg_delete(trunk_name, reg_id)
        logger.info("SIP reg deleted: trunk=%r id=%r by %r",
                    trunk_name, reg_id, session.get("username"))
    except atp_client.AtpBackendError as exc:
        logger.error("SIP reg delete failed: trunk=%r id=%r: %s", trunk_name, reg_id, exc)
    return RedirectResponse(url=f"/trunks/{trunk_name}/sipreg", status_code=303)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _form_to_payload(form) -> dict:
    monitoring = form.get("monitoring_method", "None")
    protocol   = form.get("sip_protocol", "UDP")
    trunk_type = form.get("type", "sip").lower()  # C++ expects lowercase: "sip", "lara_mrd", "cisco_mrd"
    return {
        # ── Identity ──────────────────────────────────────────────────────────
        "name":              form.get("name", "").strip(),
        "type":              trunk_type,
        "gateways":          form.getlist("gateways"),
        # ── Matching / translation ────────────────────────────────────────────
        "trunk_match":       form.get("trunk_match", "").strip(),
        "translation_match": form.get("translation_match", "").strip(),
        "translation_sub":   form.get("translation_sub", "").strip(),
        "strip_front_count": int(form.get("strip_front_count", 0) or 0),
        "strip_end_count":   int(form.get("strip_end_count", 0) or 0),
        "add_prefix":        form.get("add_prefix", "").strip(),
        "add_postfix":       form.get("add_postfix", "").strip(),
        # ── SIP addressing ────────────────────────────────────────────────────
        # C++ uses "pstn" for the external SIP gateway address
        "pstn":              form.get("external_sip_address", "").strip(),
        "local_sip_address": form.get("local_sip_address", "").strip(),
        "public_sip_address": form.get("public_sip_address", "").strip(),
        # ── SIP credentials ───────────────────────────────────────────────────
        "sip_remote_username": form.get("sip_remote_username", "").strip(),
        "sip_remote_password": form.get("sip_remote_password", ""),
        "sip_remote_realm":    form.get("sip_remote_realm", "").strip(),
        "sip_local_username":  form.get("sip_local_username", "").strip(),
        "sip_local_password":  form.get("sip_local_password", ""),
        # ── Protocol / monitoring (C++ enum string names) ─────────────────────
        "sip_trunk_protocol":           _SIP_PROTOCOL_MAP.get(protocol, "UDP"),
        "sip_trunk_monitoring_method":  _MONITORING_METHOD_MAP.get(monitoring, "None"),
        # C++ JSON parser has no boolean support — send 0/1, not true/false
        "auth_incoming_call":           1 if form.get("auth_incoming_call") else 0,
    }


def _validate_trunk(payload: dict, is_add: bool) -> list:
    errors = []
    if is_add and not payload.get("name"):
        errors.append("Name is required.")
    return errors


async def _gateway_lists(selected: list | None) -> tuple[list, list]:
    """Return (available_gateways, selected_gateways) by subtracting selected from all."""
    try:
        all_gateways = await atp_client.gateway_search()
        all_names = [g.get("name", g) if isinstance(g, dict) else g for g in all_gateways]
    except atp_client.AtpBackendError:
        all_names = []

    selected = selected or []
    available = [g for g in all_names if g not in selected]
    return available, selected


async def _safe_trunk_search() -> list:
    try:
        return await atp_client.trunk_search()
    except atp_client.AtpBackendError as exc:
        logger.error("Trunk search failed: %s", exc)
        return []


async def _safe_trunk_get(name: str) -> dict:
    try:
        return await atp_client.trunk_get(name)
    except atp_client.AtpBackendError as exc:
        logger.error("Trunk get failed: %r: %s", name, exc)
        return {"name": name}


async def _safe_sipreg_list(trunk_name: str) -> list:
    try:
        return await atp_client.sipreg_search(trunk_name)
    except atp_client.AtpBackendError as exc:
        logger.error("SIP reg list failed: trunk=%r: %s", trunk_name, exc)
        return []


async def _safe_sipreg_get(trunk_name: str, reg_id: str) -> dict:
    try:
        return await atp_client.sipreg_get(trunk_name, reg_id)
    except atp_client.AtpBackendError as exc:
        logger.error("SIP reg get failed: trunk=%r id=%r: %s", trunk_name, reg_id, exc)
        return {}
