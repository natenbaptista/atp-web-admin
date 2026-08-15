"""
session.py — session helpers shared by main.py and all routers.

Extracted from main.py so routers can import without circular dependency.
All session state lives in a signed cookie (itsdangerous).
"""

import os
from typing import Optional

from fastapi import Cookie, HTTPException, Request
from itsdangerous import URLSafeTimedSerializer

# ── Config (mirrors main.py env vars) ────────────────────────────────────────

DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production-please")
INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "enePath")


def _read_app_version() -> str:
    """
    Read AMP_VERSION from ~/atp/deploy/version.txt.
    Falls back to the env var APP_VERSION, then a hardcoded default.
    """
    env_ver = os.environ.get("APP_VERSION", "")
    if env_ver:
        return env_ver
    candidates = [
        "/home/atp/atp/deploy/version.txt",          # well-known install path
        os.path.expanduser("~/atp/deploy/version.txt"),
        "/opt/enepath/deploy/version.txt",
        os.path.join(os.environ.get("ATPMGR_DATADIR", ""), "..", "deploy", "version.txt"),
    ]
    import re as _re
    for path in candidates:
        try:
            text = open(path).read()
            m = _re.search(r'AMP_VERSION="([^"]+)"', text)
            if m:
                return m.group(1)
        except OSError:
            pass
    return "2.3.8.4"


APP_VERSION = _read_app_version()

SESSION_COOKIE = "atp_session"
SESSION_MAX_AGE_SECONDS = 8 * 60 * 60  # 8 hours

_signer = URLSafeTimedSerializer(SECRET_KEY, salt="atp-session")


# ── Cookie flags ──────────────────────────────────────────────────────────────

def cookie_flags() -> dict:
    return {
        "httponly": True,
        "secure": not DEV_MODE,
        "samesite": "lax",
    }


# ── Session encode / decode ───────────────────────────────────────────────────

def make_session(user: dict) -> str:
    return _signer.dumps({
        "username":   user.get("username", ""),
        "first_name": user.get("first_name", ""),
        "last_name":  user.get("last_name", ""),
        "role":       user.get("role", ""),
        "guid":       user.get("guid", ""),
    })


def read_session(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    try:
        return _signer.loads(raw, max_age=SESSION_MAX_AGE_SECONDS)
    except Exception:
        return None


# ── FastAPI dependency: optional session ──────────────────────────────────────

def get_session(atp_session: Optional[str] = Cookie(default=None)) -> Optional[dict]:
    """Returns session dict or None. Use when unauthenticated access is allowed."""
    return read_session(atp_session)


# ── FastAPI dependency: required session ──────────────────────────────────────

def require_session(atp_session: Optional[str] = Cookie(default=None)) -> dict:
    """Returns session dict or raises 401. Use in protected routes."""
    session = read_session(atp_session)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    return session


# ── JSON vs HTML response detection ──────────────────────────────────────────

def wants_json(request: Request) -> bool:
    """True when the caller prefers a JSON response (React SPA via apiPost)."""
    return "application/json" in request.headers.get("accept", "")


# ── Template context helper ───────────────────────────────────────────────────

def template_ctx(request: Request, session: Optional[dict], **extra) -> dict:
    """
    Build the common Jinja2 context dict for templates that extend base.html.
    Every router view should spread this into its TemplateResponse context.

    Usage:
        return templates.TemplateResponse("foo/index.html",
            template_ctx(request, session, items=items))
    """
    csrf_token = getattr(request.state, "csrf_token", "")
    # starlette-csrf cookie — echoed for data-atp-csrf on POST forms (see base.html)
    csrf_cookie = ""
    try:
        raw = request.cookies.get("csrftoken", "")
        csrf_cookie = raw if isinstance(raw, str) else ""
    except Exception:
        csrf_cookie = ""
    return {
        "request":       request,
        "current_user":  session or {},
        "instance_name": INSTANCE_NAME,
        "app_version":   APP_VERSION,
        "csrf_token":    csrf_token,
        "csrf_cookie":   csrf_cookie,
        **extra,
    }
