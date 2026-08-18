"""
session.py — session helpers shared by main.py and all routers.

Extracted from main.py so routers can import without circular dependency.
All session state lives in a signed cookie (itsdangerous).
"""

import os
from pathlib import Path
from typing import Optional

from fastapi import Cookie, HTTPException, Request
from itsdangerous import URLSafeTimedSerializer

# ── Config (mirrors main.py env vars) ────────────────────────────────

DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production-please")
INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "enePath")


def _read_app_version() -> str:
    """
    AMP version from ~/atp/deploy/version.txt (AMP_VERSION="...").
    Env APP_VERSION is only a fallback when no file is found — a stale
    service env like 3.0.0.7 must not win over the live file.
    """
    candidates = [
        "/home/atp/atp/deploy/version.txt",          # well-known install path
        os.path.expanduser("~/atp/deploy/version.txt"),
        "/opt/enepath/deploy/version.txt",
        os.path.join(os.environ.get("ATPMGR_DATADIR", ""), "..", "deploy", "version.txt"),
    ]
    import re as _re
    for path in candidates:
        if not path:
            continue
        try:
            text = open(path).read()
            m = _re.search(r'AMP_VERSION="([^"]+)"', text)
            if m:
                return m.group(1)
        except OSError:
            pass
    env_ver = os.environ.get("APP_VERSION", "")
    if env_ver:
        return env_ver
    return "2.3.8.4"


def get_app_version() -> str:
    """Re-read version.txt on each call so a new AMP build shows without restart."""
    return _read_app_version()


def _read_web_version() -> str:
    """Read amp_web_version next to this file. Independent of AMP / APP_VERSION."""
    path = Path(__file__).parent / "amp_web_version"
    try:
        return path.read_text(encoding="utf-8").strip() or "0"
    except OSError:
        return "0"


def get_web_version() -> str:
    return _read_web_version()


# Snapshots for startup logs. Routes should call get_* so they re-read.
APP_VERSION = _read_app_version()
WEB_VERSION = _read_web_version()

SESSION_COOKIE = "atp_session"
SESSION_MAX_AGE_SECONDS = 8 * 60 * 60  # 8 hours

_signer = URLSafeTimedSerializer(SECRET_KEY, salt="atp-session")
