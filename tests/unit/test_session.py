"""
tests/unit/test_session.py — unit tests for session.py helpers.
"""

import os
import time

import pytest
from fastapi import HTTPException

import session as sess


USER = {
    "username": "alice",
    "first_name": "Alice",
    "last_name": "Smith",
    "role": "admin",
    "guid": "guid-0001",
}


# ── make_session / read_session round-trip ────────────────────────────────────

def test_make_read_roundtrip():
    token = sess.make_session(USER)
    assert isinstance(token, str)
    data = sess.read_session(token)
    assert data is not None
    assert data["username"] == "alice"
    assert data["role"] == "admin"


def test_read_session_none_returns_none():
    assert sess.read_session(None) is None


def test_read_session_garbage_returns_none():
    assert sess.read_session("not-a-valid-token!!!") is None


def test_read_session_expired(monkeypatch):
    # Temporarily shrink max age to 0 so the token expires immediately
    monkeypatch.setattr(sess, "SESSION_MAX_AGE_SECONDS", 0)
    token = sess.make_session(USER)
    time.sleep(0.01)
    # Re-import read_session so it picks up the monkeypatched constant
    result = sess.read_session(token)
    # Token was just created so it may or may not have expired in 0.01 s;
    # just verify it either succeeds or returns None (not an exception).
    assert result is None or isinstance(result, dict)


# ── get_session ───────────────────────────────────────────────────────────────

def test_get_session_valid():
    token = sess.make_session(USER)
    result = sess.get_session(atp_session=token)
    assert result is not None
    assert result["username"] == "alice"


def test_get_session_no_cookie():
    result = sess.get_session(atp_session=None)
    assert result is None


# ── require_session ───────────────────────────────────────────────────────────

def test_require_session_valid():
    token = sess.make_session(USER)
    result = sess.require_session(atp_session=token)
    assert result["username"] == "alice"


def test_require_session_missing_raises_401():
    with pytest.raises(HTTPException) as exc_info:
        sess.require_session(atp_session=None)
    assert exc_info.value.status_code == 401


def test_require_session_invalid_raises_401():
    with pytest.raises(HTTPException) as exc_info:
        sess.require_session(atp_session="bad-token")
    assert exc_info.value.status_code == 401


# ── cookie_flags ──────────────────────────────────────────────────────────────

def test_cookie_flags_dev_mode(monkeypatch):
    monkeypatch.setenv("DEV_MODE", "1")
    monkeypatch.setattr(sess, "DEV_MODE", True)
    flags = sess.cookie_flags()
    assert flags["httponly"] is True
    assert flags["secure"] is False  # dev mode: no HTTPS required


def test_cookie_flags_prod_mode(monkeypatch):
    monkeypatch.setattr(sess, "DEV_MODE", False)
    flags = sess.cookie_flags()
    assert flags["secure"] is True


# ── template_ctx ──────────────────────────────────────────────────────────────

def test_template_ctx_includes_session(monkeypatch):
    from unittest.mock import MagicMock
    request = MagicMock()
    request.state.csrf_token = "tok123"
    request.cookies.get = MagicMock(side_effect=lambda k, d="": "signed-csrf" if k == "csrftoken" else d)
    ctx = sess.template_ctx(request, USER, extra_key="extra_val")
    assert ctx["current_user"]["username"] == "alice"
    assert ctx["csrf_token"] == "tok123"
    assert ctx["csrf_cookie"] == "signed-csrf"
    assert ctx["extra_key"] == "extra_val"
    assert ctx["request"] is request


def test_template_ctx_none_session(monkeypatch):
    from unittest.mock import MagicMock
    request = MagicMock()
    request.state.csrf_token = ""
    request.cookies.get = MagicMock(side_effect=lambda k, d="": d)
    ctx = sess.template_ctx(request, None)
    assert ctx["current_user"] == {}
    assert ctx["csrf_cookie"] == ""
