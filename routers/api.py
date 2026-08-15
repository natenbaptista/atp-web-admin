"""
routers/api.py — /api/* and /oauth/* endpoints

Two distinct surfaces:

1. Machine-to-machine API used by turrets and external clients
   POST /api/user/login                   → authenticate, return user dict
   GET  /api/health                       → liveness (no auth required)
   GET  /api/vr-servers                   → list VR/recording servers
   GET  /api/groups                       → list available API groups + methods
   GET  /api/admin                        → alias: list all REST API users
   POST /api/lines                        → list all lines
   POST /api/user-lines                   → list lines for a specific user
   POST /api/inbound-rules                → list all inbound rules
   POST /api/outbound-rules               → list all outbound rules
   POST /api/cdr                          → get call detail records
   POST /api/remote-monitored-nodes       → write monitored_nodes file
   POST /api/connected-nodes              → get currently connected nodes
   POST /api/nodes-to-monitor             → get + consume nodes-to-monitor queue
   POST /api/log-monitoring-change        → log a monitoring config change

2. OAuth token endpoints (external clients call these for M2M access)
   POST /oauth/get-access-token           → exchange client_id+secret → token
   POST /oauth/regenerate-access-token    → exchange refresh_token → new token
   POST /oauth/verify-token               → verify access_token (+ api_name)
   GET  /oauth/authorize                  → OAuth2 authorize endpoint
   POST /oauth/logout                     → invalidate session
   POST /oauth/reset-password             → change API user password

3. REST API user management (admin UI)
   GET  /api/users                        → list all REST API users
   POST /api/users/add                    → create user; returns {client_id, client_secret}
   POST /api/users/{username}/edit        → update email
   POST /api/users/{username}/delete      → delete user
   GET  /api/users/{username}/credentials → get client_id
   POST /api/users/{username}/regenerate-credentials → new client_id + secret
   POST /api/generate-credentials        → regenerate creds for the calling user
   POST /api/add-credentials             → register API groups (one-time setup)

Ports: OauthsController.php (1508 lines)
"""

import asyncio
import os

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

import atp_client
from session import (
    require_session, make_session, cookie_flags, SESSION_COOKIE, get_session,
)
from logging_config import logger

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)

DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")

# ── Static API group definitions (mirrors PHP add_api_credentials) ─────────────

_API_GROUPS = {
    "user":       ["login_authentication"],
    "config":     [
        "list_all_users", "list_all_lines", "list_user_lines",
        "list_all_inbound_rules", "list_all_outbound_rules",
    ],
    "CDR":        ["get_CDR_list"],
    "VR":         ["get_vr_server_list"],
    "monitoring": [
        "remote_monitored_nodes", "get_connected_nodes",
        "get_nodes_to_monitor", "log_monitoring_change",
    ],
}


# ══════════════════════════════════════════════════════════════════════════════
# Machine-to-machine helpers
# ══════════════════════════════════════════════════════════════════════════════

def _error(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"result": "fail", "message": msg}, status_code=status)


def _ok(data) -> JSONResponse:
    return JSONResponse({"result": "success", "data": data})


async def _parse_m2m_body(request: Request) -> dict:
    """Accept JSON or URL-encoded body for M2M endpoints."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            return await request.json()
        except Exception:
            return {}
    if "application/x-www-form-urlencoded" in content_type or "multipart" in content_type:
        form = await request.form()
        return dict(form)
    # Try JSON fallback
    try:
        return await request.json()
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# /api — Admin / health / data endpoints
# ══════════════════════════════════════════════════════════════════════════════

api_router = APIRouter(prefix="/api")


@api_router.post("/user/login", response_class=JSONResponse)
@limiter.limit("10/minute")
async def api_user_login(request: Request):
    """
    Authenticate a user and return their profile as JSON.
    Accepts JSON body: {"username": "...", "password": "..."}
    Used by turrets for machine-to-machine login (mirrors PHP RestUserController).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if not username or not password:
        return JSONResponse({"error": "username and password are required"}, status_code=400)

    client_ip = request.client.host if request.client else "unknown"
    backend_available = bool(os.environ.get("ATPMGR_DATADIR"))

    try:
        if backend_available and not DEV_MODE:
            user = await atp_client.authenticate(username, password)
        else:
            DEV_USERS = {"admin": "admin"}
            if DEV_USERS.get(username) == password:
                user = {"username": username, "first_name": "Dev",
                        "last_name": "User", "role": "admin", "guid": "dev"}
            else:
                raise ValueError("Invalid username or password.")

        logger.info("API login success: user=%r ip=%s", username, client_ip)
        response = JSONResponse({
            "username":   user.get("username", ""),
            "first_name": user.get("first_name", ""),
            "last_name":  user.get("last_name", ""),
            "role":       user.get("role", ""),
            "guid":       user.get("guid", ""),
            "email":      user.get("email", ""),
        })
        response.set_cookie(SESSION_COOKIE, make_session(user), **cookie_flags())
        return response

    except ValueError as exc:
        logger.warning("API login failed: user=%r ip=%s reason=%s", username, client_ip, exc)
        return JSONResponse({"error": str(exc)}, status_code=401)

    except atp_client.AtpBackendError as exc:
        logger.error("API login backend error: user=%r ip=%s error=%s", username, client_ip, exc)
        return JSONResponse({"error": f"Backend unavailable: {exc}"}, status_code=503)


@api_router.get("/health", response_class=JSONResponse)
async def api_health():
    """Quick liveness check — no authentication required."""
    return JSONResponse({"status": "ok"})


@api_router.get("/vr-servers", response_class=JSONResponse)
async def api_vr_servers(request: Request):
    """
    Return list of VR (voice-recording) servers.
    Requires a valid access_token in the request body or query param.
    Ports: get_vr_server_list() in OauthsController.php.
    """
    obj = await _parse_m2m_body(request)
    access_token = (
        obj.get("access_token")
        or request.query_params.get("access_token")
        or ""
    )
    if not access_token:
        return _error("access_token is required.", 401)

    # Verify token before serving data
    try:
        await atp_client.rest_token_verify(access_token, "get_vr_server_list")
    except atp_client.AtpBackendError:
        return _error("User does not have valid access rights.", 403)

    try:
        sites = await atp_client.site_search()
        vr_servers = [
            {"server_name": s.get("name", ""), "server_ip": s.get("ip", "")}
            for s in sites.values()
            if s.get("type", "") in ("vr", "recording", "VR") or "vr" in s.get("name", "").lower()
        ]
        return _ok(vr_servers)
    except atp_client.AtpBackendError as exc:
        return _error(str(exc), 502)


@api_router.get("/groups", response_class=JSONResponse)
async def api_groups(session: dict = Depends(require_session)):
    """Return the available API groups and their associated methods."""
    return [
        {"name": group, "methods": methods}
        for group, methods in _API_GROUPS.items()
    ]


@api_router.get("/admin", response_class=JSONResponse)
async def api_admin(session: dict = Depends(require_session)):
    """Admin page alias — returns all REST API users."""
    try:
        return await atp_client.api_user_search()
    except atp_client.AtpBackendError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


# ── REST API user management ───────────────────────────────────────────────────

@api_router.get("/users", response_class=JSONResponse)
async def api_users_list(session: dict = Depends(require_session)):
    """List all REST API users."""
    try:
        return await atp_client.api_user_search()
    except atp_client.AtpBackendError as exc:
        logger.error("API user list failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=502)


@api_router.post("/users/add", response_class=JSONResponse)
async def api_users_add(request: Request, session: dict = Depends(require_session)):
    """Create a new REST API user; returns {client_id, client_secret}."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    username = (body.get("username") or "").strip()
    email    = (body.get("email") or "").strip()
    if not username:
        return JSONResponse({"error": "username is required"}, status_code=400)

    try:
        result = await atp_client.api_user_create({"username": username, "email": email})
        logger.info("API user created: %r by %r", username, session.get("username"))
        return result
    except atp_client.AtpBackendError as exc:
        logger.error("API user create failed: %r: %s", username, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)


@api_router.post("/users/{username}/edit", response_class=JSONResponse)
async def api_users_edit(
    username: str,
    request: Request,
    session: dict = Depends(require_session),
):
    """Update a REST API user (e.g. email)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    try:
        await atp_client.api_user_update(username, body)
        logger.info("API user updated: %r by %r", username, session.get("username"))
        return {"message": f"User '{username}' updated."}
    except atp_client.AtpBackendError as exc:
        logger.error("API user update failed: %r: %s", username, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)


@api_router.post("/users/{username}/delete", response_class=JSONResponse)
async def api_users_delete(username: str, session: dict = Depends(require_session)):
    """Delete a REST API user."""
    try:
        await atp_client.api_user_delete(username)
        logger.info("API user deleted: %r by %r", username, session.get("username"))
        return {"message": f"User '{username}' deleted."}
    except atp_client.AtpBackendError as exc:
        logger.error("API user delete failed: %r: %s", username, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)


@api_router.get("/users/{username}/credentials", response_class=JSONResponse)
async def api_users_credentials(username: str, session: dict = Depends(require_session)):
    """Get credentials (client_id only) for a REST API user."""
    try:
        return await atp_client.api_user_credentials_get(username)
    except atp_client.AtpBackendError as exc:
        logger.error("API user credentials get failed: %r: %s", username, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)


@api_router.post("/users/{username}/regenerate-credentials", response_class=JSONResponse)
async def api_users_regenerate(username: str, session: dict = Depends(require_session)):
    """Regenerate client_id and client_secret for a REST API user."""
    try:
        result = await atp_client.api_user_regenerate_credentials(username)
        logger.info("API credentials regenerated: %r by %r", username, session.get("username"))
        return result
    except atp_client.AtpBackendError as exc:
        logger.error("API credentials regenerate failed: %r: %s", username, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)


@api_router.post("/generate-credentials", response_class=JSONResponse)
async def api_generate_credentials(
    request: Request,
    session: dict = Depends(require_session),
):
    """
    Generate (or regenerate) client credentials for a given username.
    Ports: generate_client_credentials() in OauthsController.php.
    Body: {"username": "..."}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    username = (body.get("username") or session.get("username", "")).strip()
    if not username:
        return JSONResponse({"error": "username is required"}, status_code=400)

    try:
        result = await atp_client.api_user_regenerate_credentials(username)
        logger.info("Credentials generated for: %r by %r", username, session.get("username"))
        return result
    except atp_client.AtpBackendError as exc:
        logger.error("Generate credentials failed: %r: %s", username, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)


@api_router.post("/add-credentials", response_class=JSONResponse)
async def api_add_credentials(session: dict = Depends(require_session)):
    """
    One-time registration of standard API groups.
    Ports: add_api_credentials() in OauthsController.php.
    Returns the group definitions that were registered.
    """
    # In the Python/ATP architecture the groups are defined statically.
    # This endpoint confirms registration and returns the group map.
    return {
        "result": "success",
        "groups": _API_GROUPS,
        "message": "API groups registered successfully.",
    }


# ── M2M data endpoints (config / CDR / monitoring) ───────────────────────────

@api_router.post("/lines", response_class=JSONResponse)
async def api_list_all_lines(request: Request):
    """
    Return all lines.
    Ports: list_all_lines() in OauthsController.php.
    Body: {access_token}
    """
    obj = await _parse_m2m_body(request)
    access_token = (obj.get("access_token") or "").strip()
    if not access_token:
        return _error("access_token is required.", 401)
    try:
        await atp_client.rest_token_verify(access_token, "list_all_lines")
    except atp_client.AtpBackendError:
        return _error("User does not have valid access rights.", 403)
    try:
        lines = await atp_client.line_search()
        result = [
            {
                "name":               l.get("name", l.get("dn", "")),
                "description":        l.get("description", l.get("desc", "")),
                "capacity":           l.get("capacity", ""),
                "forwarding_enabled": l.get("forwarding_enabled", False),
                "requires_external":  l.get("require_external_on_call", l.get("requires_external", False)),
                "do_not_record":      l.get("do_not_record", False),
            }
            for l in lines
        ]
        return _ok(result)
    except atp_client.AtpBackendError as exc:
        return _error(str(exc), 502)


@api_router.post("/user-lines", response_class=JSONResponse)
async def api_list_user_lines(request: Request):
    """
    Return lines assigned to a specific user.
    Ports: list_user_lines() in OauthsController.php.
    Body: {access_token, amp_user_name}
    """
    obj = await _parse_m2m_body(request)
    access_token  = (obj.get("access_token") or "").strip()
    amp_user_name = (obj.get("amp_user_name") or "").strip()
    if not access_token:
        return _error("access_token is required.", 401)
    if not amp_user_name:
        return _error("amp_user_name is required.", 400)
    try:
        await atp_client.rest_token_verify(access_token, "list_user_lines")
    except atp_client.AtpBackendError:
        return _error("User does not have valid access rights.", 403)
    try:
        lines = await atp_client.line_search()

        async def _line_has_user(line: dict) -> str | None:
            name = line.get("name", line.get("dn", ""))
            if not name:
                return None
            try:
                usernames = await atp_client.line_usernames_on_line(name)
                return name if amp_user_name in usernames else None
            except atp_client.AtpBackendError:
                return None

        results = await asyncio.gather(*[_line_has_user(l) for l in lines])
        user_lines = [r for r in results if r is not None]
        if not user_lines:
            return _error(f"No lines configured for user: {amp_user_name}")
        return _ok({"lines_configured": user_lines})
    except atp_client.AtpBackendError as exc:
        return _error(str(exc), 502)


@api_router.post("/inbound-rules", response_class=JSONResponse)
async def api_list_all_inbound_rules(request: Request):
    """
    Return all inbound rules.
    Ports: list_all_inbound_rules() in OauthsController.php.
    Body: {access_token}
    """
    obj = await _parse_m2m_body(request)
    access_token = (obj.get("access_token") or "").strip()
    if not access_token:
        return _error("access_token is required.", 401)
    try:
        await atp_client.rest_token_verify(access_token, "list_all_inbound_rules")
    except atp_client.AtpBackendError:
        return _error("User does not have valid access rights.", 403)
    try:
        rules = await atp_client.inbound_search()
        result = [
            {
                "name":            r.get("name", r.get("m_name", "")),
                "dial_plan_match": r.get("dial_plan_match", r.get("m_dial_plan_match", "")),
                "Trunks":          r.get("trunk_name", r.get("m_trunk_name", "")),
            }
            for r in rules
        ]
        return _ok(result)
    except atp_client.AtpBackendError as exc:
        return _error(str(exc), 502)


@api_router.post("/outbound-rules", response_class=JSONResponse)
async def api_list_all_outbound_rules(request: Request):
    """
    Return all outbound rules.
    Ports: list_all_outbound_rules() in OauthsController.php.
    Body: {access_token}
    """
    obj = await _parse_m2m_body(request)
    access_token = (obj.get("access_token") or "").strip()
    if not access_token:
        return _error("access_token is required.", 401)
    try:
        await atp_client.rest_token_verify(access_token, "list_all_outbound_rules")
    except atp_client.AtpBackendError:
        return _error("User does not have valid access rights.", 403)
    try:
        rules = await atp_client.outbound_search()
        result = [
            {
                "name":              r.get("name", r.get("m_name", "")),
                "translation_match": r.get("translation_match", r.get("m_translation_match", "")),
                "translation_sub":   r.get("translation_sub", r.get("m_translation_sub", "")),
                "dial_plan_match":   r.get("dial_plan_match", r.get("m_dial_plan_match", "")),
                "Routes":            r.get("route", r.get("m_route", "")),
            }
            for r in rules
        ]
        return _ok(result)
    except atp_client.AtpBackendError as exc:
        return _error(str(exc), 502)


@api_router.post("/cdr", response_class=JSONResponse)
async def api_get_cdr_list(request: Request):
    """
    Return call detail records with optional filters.
    Ports: get_CDR_list() + get_call_records() in OauthsController.php.
    Body: {access_token, start_date?, end_date?, owner?, participants?, vri?,
           cli?, line?, include_icm?, answered?, unanswered?, page?, directions?}
    """
    obj = await _parse_m2m_body(request)
    access_token = (obj.get("access_token") or "").strip()
    if not access_token:
        return _error("access_token is required.", 401)
    try:
        await atp_client.rest_token_verify(access_token, "get_CDR_list")
    except atp_client.AtpBackendError:
        return _error("User does not have valid access rights.", 403)

    filters: dict = {}
    for key in ("start_date", "end_date", "owner", "participants", "vri", "cli", "line", "page"):
        if obj.get(key):
            filters[key] = obj[key]
    for flag in ("include_icm", "answered", "unanswered"):
        if obj.get(flag) is not None:
            filters[flag] = str(obj[flag]) in ("1", "true", "True")
    if obj.get("directions"):
        filters["directions"] = obj["directions"]

    try:
        records = await atp_client.call_log_search(filters)
        result = [
            {
                "vri":          r.get("vri", ""),
                "begin_date":   r.get("begin", r.get("begin_date", "")),
                "duration":     r.get("duration", ""),
                "owner":        r.get("owner", ""),
                "participants": r.get("participants", ""),
                "direction":    r.get("direction", ""),
                "lines":        r.get("lines", ""),
                "cli":          r.get("cli", ""),
                "progress":     r.get("progress", ""),
            }
            for r in records
        ]
        return _ok(result)
    except atp_client.AtpBackendError as exc:
        return _error(str(exc), 502)


@api_router.post("/remote-monitored-nodes", response_class=JSONResponse)
async def api_remote_monitored_nodes(request: Request):
    """
    Write a list of monitored nodes to the monitored_nodes file.
    Ports: remote_monitored_nodes() in OauthsController.php.
    Body: {access_token, monitored_nodes_list}
    """
    obj = await _parse_m2m_body(request)
    access_token         = (obj.get("access_token") or "").strip()
    monitored_nodes_list = obj.get("monitored_nodes_list", "")
    if not access_token:
        return _error("access_token is required.", 401)
    if not monitored_nodes_list:
        return _error("monitored_nodes_list is required.", 400)
    try:
        await atp_client.rest_token_verify(access_token, "remote_monitored_nodes")
    except atp_client.AtpBackendError:
        return _error("User does not have valid access rights.", 403)

    datadir = os.environ.get("ATPMGR_DATADIR", "")
    if not datadir:
        return _error("ATPMGR_DATADIR not configured.", 500)
    fpath = os.path.join(datadir, "monitored_nodes")
    try:
        def _write():
            with open(fpath, "w") as f:
                f.write(str(monitored_nodes_list))
        await asyncio.to_thread(_write)
        return _ok("File Writing successful")
    except OSError as exc:
        return _error(f"File Not Written: {exc}", 500)


@api_router.post("/connected-nodes", response_class=JSONResponse)
async def api_get_connected_nodes(request: Request):
    """
    Return currently connected nodes.
    Ports: get_connected_nodes() in OauthsController.php.
    Body: {access_token}
    """
    obj = await _parse_m2m_body(request)
    access_token = (obj.get("access_token") or "").strip()
    if not access_token:
        return _error("access_token is required.", 401)
    try:
        await atp_client.rest_token_verify(access_token, "get_connected_nodes")
    except atp_client.AtpBackendError:
        return _error("User does not have valid access rights.", 403)
    try:
        connections = await atp_client.node_connections()
        return _ok(connections)
    except atp_client.AtpBackendError as exc:
        return _error(str(exc), 502)


@api_router.post("/nodes-to-monitor", response_class=JSONResponse)
async def api_get_nodes_to_monitor(request: Request):
    """
    Return the nodes queued for monitoring (reads and deletes conn_file).
    Ports: get_nodes_to_monitor() in OauthsController.php.
    Body: {access_token}
    """
    obj = await _parse_m2m_body(request)
    access_token = (obj.get("access_token") or "").strip()
    if not access_token:
        return _error("access_token is required.", 401)
    try:
        await atp_client.rest_token_verify(access_token, "get_nodes_to_monitor")
    except atp_client.AtpBackendError:
        return _error("User does not have valid access rights.", 403)

    datadir = os.environ.get("ATPMGR_DATADIR", "")
    fpath   = os.path.join(datadir, "conn_file") if datadir else ""
    if not fpath or not os.path.exists(fpath):
        return _ok(["-"])
    try:
        def _read_and_delete():
            with open(fpath, "r") as f:
                content = f.read()
            os.unlink(fpath)
            return content
        content = await asyncio.to_thread(_read_and_delete)
        return _ok([n for n in content.split("\n") if n])
    except OSError as exc:
        return _error(str(exc), 500)


@api_router.post("/log-monitoring-change", response_class=JSONResponse)
async def api_log_monitoring_change(request: Request):
    """
    Log a monitoring configuration change.
    Ports: log_monitoring_change() in OauthsController.php.
    Body: {access_token, date, node_cn, node_type, host, operation}
    """
    obj = await _parse_m2m_body(request)
    access_token = (obj.get("access_token") or "").strip()
    if not access_token:
        return _error("access_token is required.", 401)
    try:
        await atp_client.rest_token_verify(access_token, "log_monitoring_change")
    except atp_client.AtpBackendError:
        return _error("User does not have valid access rights.", 403)
    try:
        await atp_client.monitoring_log_add(
            date      = str(obj.get("date", "")),
            node_cn   = str(obj.get("node_cn", "")),
            node_type = str(obj.get("node_type", "")),
            host      = str(obj.get("host", "")),
            operation = str(obj.get("operation", "")),
        )
        return _ok(["logged"])
    except atp_client.AtpBackendError as exc:
        return _error(str(exc), 502)


# ══════════════════════════════════════════════════════════════════════════════
# /oauth — Token + authorization endpoints
# ══════════════════════════════════════════════════════════════════════════════

oauth_router = APIRouter(prefix="/oauth")


@oauth_router.post("/get-access-token", response_class=JSONResponse)
@limiter.limit("20/minute")
async def oauth_get_access_token(request: Request):
    """
    Exchange client_id + client_secret for an access token.
    Ports: get_access_token() in OauthsController.php.

    Body (JSON or form-encoded):
      client_id     — required
      client_secret — required
    """
    obj = await _parse_m2m_body(request)

    client_id     = (obj.get("client_id") or "").strip()
    client_secret = (obj.get("client_secret") or "").strip()

    if not client_id or not client_secret:
        return _error("client_id and client_secret are required.")

    try:
        token_data = await atp_client.rest_token_get(client_id, client_secret)
        if not token_data:
            return _error("Invalid access token.", 401)
        return _ok(token_data)
    except atp_client.AtpBackendError as exc:
        logger.error("oauth get-access-token error: client_id=%r: %s", client_id, exc)
        return _error(str(exc), 502)


@oauth_router.post("/regenerate-access-token", response_class=JSONResponse)
@limiter.limit("20/minute")
async def oauth_regenerate_access_token(request: Request):
    """
    Exchange a refresh token for a new access token.
    Ports: regenerate_access_token() in OauthsController.php.

    Body (JSON or form-encoded):
      client_id     — required
      client_secret — required
      refresh_token — required
    """
    obj = await _parse_m2m_body(request)

    client_id     = (obj.get("client_id") or "").strip()
    client_secret = (obj.get("client_secret") or "").strip()
    refresh_token = (obj.get("refresh_token") or "").strip()

    if not client_id or not client_secret:
        return _error("client_id and client_secret are required.")
    if not refresh_token:
        return _error("refresh_token is required.")

    try:
        token_data = await atp_client.rest_token_refresh(client_id, client_secret, refresh_token)
        if not token_data:
            return _error("Invalid access token.", 401)
        return _ok(token_data)
    except atp_client.AtpBackendError as exc:
        logger.error("oauth regenerate-token error: client_id=%r: %s", client_id, exc)
        return _error(str(exc), 502)


@oauth_router.post("/verify-token", response_class=JSONResponse)
@limiter.limit("60/minute")
async def oauth_verify_token(request: Request):
    """
    Verify an access token, optionally checking access rights for a named API.
    Ports: verify_access_token() in OauthsController.php.

    Body (JSON or form-encoded):
      access_token — required
      api_name     — optional; if provided, checks that the token has rights to this API
    """
    obj = await _parse_m2m_body(request)

    access_token = (obj.get("access_token") or "").strip()
    api_name     = (obj.get("api_name") or "").strip()

    if not access_token:
        return _error("access_token is required.")

    try:
        result = await atp_client.rest_token_verify(access_token, api_name)
        return _ok(result or "Token valid")
    except atp_client.AtpBackendError as exc:
        return _error("User does not have valid access rights.", 403)


@oauth_router.get("/authorize", response_class=JSONResponse)
async def oauth_authorize(
    request: Request,
    client_id: str = "",
    redirect_uri: str = "",
    response_type: str = "code",
    session: dict = Depends(require_session),
):
    """
    OAuth2 authorization endpoint — auto-approves for authenticated admins.
    Ports: authorize() in OauthsController.php.
    """
    if not client_id:
        raise HTTPException(status_code=400, detail="client_id is required")

    # In the ATP architecture, authorization is handled by the token endpoint.
    # Return the client_id as the authorization code for immediate exchange.
    return JSONResponse({
        "result": "success",
        "authorized": True,
        "client_id": client_id,
        "message": "Authorization granted",
    })


@oauth_router.post("/logout", response_class=JSONResponse)
async def oauth_logout(
    request: Request,
    session: dict = Depends(get_session),
):
    """
    Invalidate the current session.
    Ports: logout() in OauthsController.php.
    """
    from session import SESSION_COOKIE
    response = JSONResponse({"result": "success", "message": "Logged out"})
    response.delete_cookie(SESSION_COOKIE)
    if session:
        logger.info("OAuth logout: user=%r", session.get("username"))
    return response


@oauth_router.post("/reset-password", response_class=JSONResponse)
async def oauth_reset_password(
    request: Request,
    session: dict = Depends(require_session),
):
    """
    Change the current API user's password.
    Ports: reset_pwd() in OauthsController.php.

    Body (JSON or form-encoded):
      username     — required
      new_password — required
    """
    obj = await _parse_m2m_body(request)

    username     = (obj.get("username") or session.get("username", "")).strip()
    new_password = (obj.get("new_password") or "").strip()

    if not username:
        return _error("username is required.")
    if not new_password:
        return _error("new_password is required.")
    if len(new_password) < 6:
        return _error("Password must be at least 6 characters.")

    try:
        await atp_client.api_user_reset_password(username, new_password)
        logger.info("API user password reset: %r by %r", username, session.get("username"))
        return _ok("Password updated successfully.")
    except atp_client.AtpBackendError as exc:
        logger.error("API user password reset failed: %r: %s", username, exc)
        return _error(str(exc), 502)


# ── Expose both sub-routers under the shared `router` name ────────────────────
# main.py imports `directory_router.router`; we expose a single combined router.

from fastapi import APIRouter as _APIRouter

router = _APIRouter()
router.include_router(api_router)
router.include_router(oauth_router)
