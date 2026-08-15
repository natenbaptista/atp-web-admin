"""
tests/conftest.py — shared pytest fixtures for enePath WebAdmin.

Run tests:
    cd webadmin-24
    pytest tests/ -v

All tests mock the ATP backend socket so no real daemon is needed.
DEV_MODE is forced on so HTTPS redirect and real-auth are bypassed.
"""

import asyncio
import json
import os
import sys
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# ── Make app importable from tests/ ──────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Force dev mode and a stable secret key before importing the app
os.environ.setdefault("DEV_MODE", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest")
os.environ.setdefault("ATPMGR_DATADIR", "")  # empty = no socket required in dev

from main import app  # noqa: E402  (must come after env setup)
from session import make_session  # noqa: E402


# ── Session cookie helper ─────────────────────────────────────────────────────

ADMIN_USER = {
    "username": "admin",
    "first_name": "Admin",
    "last_name": "User",
    "role": "admin",
    "guid": "test-guid-0001",
}


def admin_cookie() -> dict:
    """Return cookie dict for an authenticated admin session."""
    return {"atp_session": make_session(ADMIN_USER)}


# ── ASGI test client ──────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """AsyncClient wired to the FastAPI app (no real network)."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies=admin_cookie(),
        follow_redirects=True,
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def anon_client() -> AsyncGenerator[AsyncClient, None]:
    """Unauthenticated client — for testing auth redirects."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac


# ── Backend mock helpers ──────────────────────────────────────────────────────

def make_backend_reply(msg_type: str, payload) -> bytes:
    """Build a wire-protocol reply line as the ATP backend would send."""
    return (json.dumps({"type": msg_type, "payload": payload}) + "\n").encode()


def patch_backend(reply_type: str = "node_announce_ok", payload=None):
    """
    Context manager / decorator that stubs out asyncio.open_unix_connection
    so atp_client never touches the filesystem.

    Usage:
        with patch_backend("controller_user_search", [{"username": "alice"}]):
            ...
    """
    if payload is None:
        payload = {}

    raw_reply = make_backend_reply(reply_type, payload)

    mock_reader = AsyncMock()
    mock_reader.readline = AsyncMock(return_value=raw_reply)

    from unittest.mock import MagicMock
    mock_writer = MagicMock()
    mock_writer.write = MagicMock()          # synchronous in asyncio streams
    mock_writer.close = MagicMock()          # synchronous
    mock_writer.wait_closed = AsyncMock()
    mock_writer.drain = AsyncMock()

    return patch(
        "asyncio.open_unix_connection",
        new=AsyncMock(return_value=(mock_reader, mock_writer)),
    )
