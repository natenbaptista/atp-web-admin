"""
main.py — enePath WebAdmin (FastAPI)

Replaces the PHP/CakePHP manager web interface.
Talks to the C++ ATP backend via atp_client.py (Unix socket, JSON protocol).
"""

import asyncio
import os
import pathlib
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, Form, Cookie, Response, Depends
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware

import atp_client
import db as db_module
from logging_config import setup_logging, logger
from session import (
    SESSION_COOKIE, cookie_flags, make_session, read_session, get_session, require_session,
    APP_VERSION, INSTANCE_NAME, WEB_VERSION,
    get_app_version, get_web_version,
)
from routers import dashboard as dashboard_router
from routers import users as users_router
from routers import lines as lines_router
from routers import settings as settings_router
from routers import calls as calls_router
from routers import trunks as trunks_router
from routers import inbounds as inbounds_router
from routers import outbounds as outbounds_router
from routers import audit as audit_router
from routers import topology as topology_router
from routers import backup as backup_router_mod
from routers import media as media_router
from routers import groups as groups_router
from routers import benchmarking as benchmarking_router
from routers import docs as docs_router
from routers import recording as recording_router
from routers import routes as routes_router
from routers import api as api_router
from routers import license as license_router
from routers import stations as stations_router
from routers import logs as logs_router
from routers import directory as directory_router
from routers import maintenance as maintenance_router
from button_colors import router as button_colors_router

# ── Environment ───────────────────────────────────────────────────────────────

DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")
DUMMY_LOGIN = os.environ.get("DUMMY_LOGIN", "").lower() in ("1", "true", "yes")
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production-please")
# APP_VERSION imported from session.py

# ── Logging ───────────────────────────────────────────────────────────────────
# LOG_LEVEL=DEBUG  for full wire-protocol traces and request timing
# LOG_LEVEL=INFO   for login/logout events (default)
# LOG_FILE=/var/log/webadmin/app.log to persist to disk

setup_logging()

# ── Background tasks ─────────────────────────────────────────────────────────

async def _cleanup_temp_files() -> None:
    """Delete log ZIP archives older than 1 hour — runs every hour."""
    while True:
        await asyncio.sleep(3600)
        tmp = os.path.join(os.environ.get("ATPMGR_DATADIR", ""), "tmp")
        if os.path.isdir(tmp):
            now = time.time()
            for name in os.listdir(tmp):
                path = os.path.join(tmp, name)
                try:
                    if now - os.path.getmtime(path) > 3600:
                        os.remove(path)
                        logger.info("Deleted stale temp file: %s", path)
                except OSError:
                    pass

# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_cleanup_temp_files())
    logger.info("WebAdmin started (version %s, DEV_MODE=%s, DUMMY_LOGIN=%s)", get_app_version(), DEV_MODE, DUMMY_LOGIN)
    yield
    task.cancel()
    await db_module.close_pool()
    logger.info("WebAdmin stopped")

# ── App setup ────────────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="enePath WebAdmin", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CSRF protection is provided by SameSite=Lax on the session cookie (session.py).
# For a React SPA on the same origin, this is sufficient — the browser will not
# send the session cookie on cross-origin POST requests, so CSRF attacks are blocked.

# Request timing — logs every request at DEBUG, slow requests (>1 s) at WARNING
@app.middleware("http")
async def request_timing(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    msg = "%s %s → %d  (%.3fs)" % (
        request.method, request.url.path, response.status_code, elapsed
    )
    if elapsed > 1.0:
        logger.warning("SLOW %s", msg)
    else:
        logger.debug(msg)
    return response


# Security response headers (mirrors PHP OauthsController X-Frame-Options)
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; "
        "img-src 'self' data:;"
    )
    return response


# HTTPS redirect in production only
if not DEV_MODE:
    app.add_middleware(HTTPSRedirectMiddleware)

# React SPA — built output lives in public/ (vite build outDir="../public")
# Mounted AFTER all routers are registered so API routes take priority.
PUBLIC_DIR = pathlib.Path(__file__).parent / "public"

# Mount routers
app.include_router(dashboard_router.router)
app.include_router(users_router.router)
app.include_router(lines_router.router)
app.include_router(settings_router.router)
app.include_router(calls_router.router)
app.include_router(trunks_router.router)
app.include_router(inbounds_router.router)
app.include_router(outbounds_router.router)
app.include_router(audit_router.router)
app.include_router(topology_router.router)
app.include_router(backup_router_mod.backup_router)
app.include_router(backup_router_mod.restore_router)
app.include_router(media_router.router)
app.include_router(groups_router.router)
app.include_router(benchmarking_router.router)
app.include_router(docs_router.router)
app.include_router(recording_router.router)
app.include_router(routes_router.router)
app.include_router(api_router.router)
app.include_router(license_router.router)
app.include_router(stations_router.router)
app.include_router(logs_router.router)
app.include_router(directory_router.router)
app.include_router(maintenance_router.router)
app.include_router(button_colors_router)


# ── Exception handlers ────────────────────────────────────────────────────

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        index = PUBLIC_DIR / "index.html"
        if index.exists():
            return FileResponse(index)
    return JSONResponse({"detail": str(exc.detail)}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    import traceback
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    detail = traceback.format_exc() if DEV_MODE else "Internal server error"
    return JSONResponse({"detail": detail}, status_code=500)


@app.exception_handler(atp_client.AtpBackendError)
async def backend_error_handler(request: Request, exc: atp_client.AtpBackendError):
    logger.error("ATP backend error: %s", exc)
    return JSONResponse({"detail": str(exc)}, status_code=502)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return RedirectResponse(url="/login")


@app.get("/login")
async def login_get(
    request: Request,
    atp_session: Optional[str] = Cookie(default=None),
):
    if read_session(atp_session):
        return RedirectResponse(url="/dashboard")
    index = PUBLIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"detail": "Frontend not built. Run: cd frontend && npm run build"}, status_code=503)


@app.post("/login")
@limiter.limit("10/minute")
async def login_post(request: Request):
    """
    Accepts both JSON (React SPA) and form-urlencoded (legacy) bodies.
    JSON response: returns user data + sets session cookie.
    Form response: redirects to /dashboard + sets session cookie.
    """
    content_type = request.headers.get("content-type", "")
    remember_me = None

    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid request body"}, status_code=400)
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        remember_me = body.get("remember_me")
    else:
        form = await request.form()
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))
        remember_me = form.get("remember_me")

    fail_reason = None
    user = None
    backend_available = bool(os.environ.get("ATPMGR_DATADIR"))

    client_ip = request.client.host if request.client else "unknown"
    logger.debug("Login attempt: user=%r ip=%s", username, client_ip)

    if backend_available and not DUMMY_LOGIN:
        try:
            user = await atp_client.authenticate(username, password)
        except ValueError as exc:
            fail_reason = str(exc)
            logger.warning("Login failed: user=%r ip=%s reason=%s", username, client_ip, exc)
        except atp_client.AtpBackendError as exc:
            fail_reason = f"Cannot reach ATP backend: {exc}"
            logger.error("Backend unreachable during login: user=%r ip=%s error=%s", username, client_ip, exc)
    else:
        DEV_USERS = {"admin": "admin"}
        if DEV_USERS.get(username) == password:
            user = {
                "username": username, "first_name": "Dev",
                "last_name": "User", "role": "admin", "guid": "dev",
            }
        else:
            fail_reason = "Invalid username or password."
            logger.warning("Login failed (dev): user=%r ip=%s", username, client_ip)

    if fail_reason:
        return JSONResponse({"error": fail_reason}, status_code=401)

    logger.info("Login success: user=%r ip=%s role=%s", username, client_ip, user.get("role", "?"))
    flags = cookie_flags()

    # Read instance_name from ATP general settings; fall back to env var
    instance_name = INSTANCE_NAME
    try:
        settings = await atp_client.settings_get("general")
        name = settings.get("instance_name", {}).get("value", "")
        if name:
            instance_name = name
    except Exception:
        pass

    if "application/json" in content_type:
        # React SPA: return JSON user data + set session cookie in response directly.
        response = JSONResponse({
            "username":      user.get("username", ""),
            "first_name":    user.get("first_name", ""),
            "last_name":     user.get("last_name", ""),
            "role":          user.get("role", ""),
            "guid":          user.get("guid", ""),
            "email":         user.get("email", ""),
            "app_version":   get_app_version(),
            "web_version":   get_web_version(),
            "instance_name": instance_name,
        })
        response.set_cookie(SESSION_COOKIE, make_session(user), **flags)
        if remember_me:
            response.set_cookie("remember_me_id", username, max_age=28 * 24 * 3600, **flags)
        else:
            response.delete_cookie("remember_me_id")
        return response
    else:
        # Legacy form POST: redirect to dashboard.
        redir = RedirectResponse(url="/dashboard", status_code=303)
        redir.set_cookie(SESSION_COOKIE, make_session(user), **flags)
        if remember_me:
            redir.set_cookie("remember_me_id", username, max_age=28 * 24 * 3600, **flags)
        else:
            redir.delete_cookie("remember_me_id")
        return redir


@app.get("/logout")
async def logout(
    request: Request,
    session: Optional[dict] = Depends(get_session),
):
    if session:
        logger.info("Logout: user=%r", session.get("username"))
    redir = RedirectResponse(url="/login", status_code=303)
    redir.delete_cookie(SESSION_COOKIE)
    return redir


@app.post("/reset-password", response_class=JSONResponse)
async def reset_password_post(
    request: Request,
    session: dict = Depends(require_session),
):
    """
    Allow a logged-in user to change their own password.
    Verifies the current password via authenticate() before calling reset_user_password().
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid request body."}, status_code=400)

    current_password = (body.get("current_password") or "").strip()
    new_password     = (body.get("new_password") or "").strip()

    if not current_password or not new_password:
        return JSONResponse({"error": "Both current and new password are required."}, status_code=400)

    username = session.get("username", "")

    # Verify current password first
    try:
        await atp_client.authenticate(username, current_password)
    except ValueError:
        return JSONResponse({"error": "Current password is incorrect."}, status_code=400)
    except atp_client.AtpBackendError as exc:
        return JSONResponse({"error": f"Backend error: {exc}"}, status_code=502)

    # Fetch the current user record, update password, and save via controller_user_update
    # (mirrors PHP UsersController::reset which calls user_manager->update_user())
    try:
        results = await atp_client.user_search(username)
        if not results:
            return JSONResponse({"error": "User not found."}, status_code=404)
        user = results[0]
    except atp_client.AtpBackendError as exc:
        return JSONResponse({"error": f"Failed to fetch user: {exc}"}, status_code=502)

    user["password"] = new_password
    try:
        await atp_client.user_update(user)
        logger.info("Password changed: user=%r", username)
        return {"success": True, "message": "Password changed successfully."}
    except atp_client.AtpBackendError as exc:
        logger.error("Password change failed: user=%r error=%s", username, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)


@app.get("/session-check")
async def session_check(session: Optional[dict] = Depends(get_session)):
    """
    Polled every 5 minutes by common.js to keep session alive.
    Returns 401 (triggers JS redirect to /login) if session expired.
    Mirrors PHP common.js heartbeat behaviour.
    """
    if not session:
        return Response(status_code=401)
    return {"valid": True, "username": session["username"]}



@app.get("/health")
async def health():
    """Liveness check — also probes the backend socket."""
    instance_name = INSTANCE_NAME
    status = {"app": "ok", "version": get_app_version(), "web_version": get_web_version(), "instance_name": instance_name, "backend": "not configured"}
    if os.environ.get("ATPMGR_DATADIR"):
        try:
            reply = await atp_client.request("controller_echo")
            status["backend"] = "ok" if reply else "no reply"
        except Exception as exc:
            status["backend"] = f"error: {exc}"
        try:
            settings = await atp_client.settings_get("general")
            name = settings.get("instance_name", {}).get("value", "")
            if name:
                status["instance_name"] = name
        except Exception:
            pass
    return status


# ── React SPA: static assets + catch-all ─────────────────────────────────────
# Registered LAST so every API route defined above takes priority.
# The /assets mount serves Vite's hashed JS/CSS bundles (immutable, long cache).
# The catch-all serves index.html for every other path (react-router handles routing).

if (PUBLIC_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=PUBLIC_DIR / "assets"), name="spa-assets")

@app.get("/{full_path:path}", include_in_schema=False)
async def serve_spa(full_path: str):
    """SPA catch-all — serves static files that exist in PUBLIC_DIR, otherwise returns index.html."""
    # Serve root-level static files (e.g. enepath-logo-white.png, favicon.ico, robots.txt)
    static_file = PUBLIC_DIR / full_path
    if full_path and static_file.exists() and static_file.is_file():
        return FileResponse(static_file)
    index = PUBLIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse(
        {"detail": "Frontend not built. Run: cd frontend && npm run build"},
        status_code=503,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=DEV_MODE)
