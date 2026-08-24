"""
tests/unit/test_auth_routes.py — tests for login/logout/health routes in main.py.

Uses the shared `client` (authenticated) and `anon_client` (unauthenticated)
fixtures from conftest.py.
"""

import pytest
import pytest_asyncio

from tests.conftest import patch_backend


# ── /health ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_no_backend_configured(anon_client):
    """Health endpoint returns 200 with app:ok even when no backend is set."""
    resp = await anon_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["app"] == "ok"
    assert "version" in data


# ── /login ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_page_renders(anon_client):
    resp = await anon_client.get("/login")
    assert resp.status_code == 200
    assert b"login" in resp.content.lower() or b"username" in resp.content.lower()


@pytest.mark.asyncio
async def test_login_dev_mode_success(anon_client):
    """In DEV_MODE the hardcoded admin/admin credentials succeed."""
    resp = await anon_client.post(
        "/login",
        data={"username": "admin", "password": "admin"},
        follow_redirects=True,
    )
    # Should redirect to /dashboard and return 200
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_login_dev_mode_wrong_password(anon_client):
    resp = await anon_client.post(
        "/login",
        data={"username": "admin", "password": "wrong"},
        follow_redirects=False,
    )
    assert resp.status_code == 401


# ── /logout ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_logout_clears_session(client):
    resp = await client.get("/logout", follow_redirects=False)
    # Should redirect to /login
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers.get("location", "")


# ── / root redirect ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_root_redirects_to_login(anon_client):
    resp = await anon_client.get("/", follow_redirects=False)
    assert resp.status_code in (301, 302, 307, 308)
    assert "/login" in resp.headers.get("location", "")


# ── /session-check ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_check_authenticated(client):
    resp = await client.get("/session-check")
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


@pytest.mark.asyncio
async def test_session_check_unauthenticated(anon_client):
    resp = await anon_client.get("/session-check")
    assert resp.status_code == 401


# ── /api/user/login ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_api_login_dev_mode(anon_client):
    """REST API login works in DEV_MODE with admin/admin."""
    resp = await anon_client.post(
        "/api/user/login",
        json={"username": "admin", "password": "admin"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "admin"
    assert "role" in data


@pytest.mark.asyncio
async def test_api_login_wrong_credentials(anon_client):
    resp = await anon_client.post(
        "/api/user/login",
        json={"username": "admin", "password": "nope"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_api_health(anon_client):
    resp = await anon_client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── Password change / first-login flag ────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_reports_must_change_password(anon_client, tmp_path, monkeypatch):
    monkeypatch.setenv("MUST_CHANGE_PASSWORD_FILE", str(tmp_path / "flags.json"))
    import password_policy
    password_policy.reset_store()
    resp = await anon_client.post(
        "/login",
        json={"username": "admin", "password": "admin"},
    )
    assert resp.status_code == 200
    assert resp.json().get("must_change_password") is False

    password_policy.mark_must_change("admin")
    resp = await anon_client.post(
        "/login",
        json={"username": "admin", "password": "admin"},
    )
    assert resp.status_code == 200
    assert resp.json().get("must_change_password") is True


@pytest.mark.asyncio
async def test_session_check_includes_must_change_false(client):
    resp = await client.get("/session-check")
    assert resp.status_code == 200
    assert resp.json()["must_change_password"] is False


@pytest.mark.asyncio
async def test_reset_password_rejects_weak_new_password(client):
    resp = await client.post(
        "/reset-password",
        json={"current_password": "admin", "new_password": "weak"},
    )
    assert resp.status_code == 400
    assert "letter" in resp.json()["error"].lower() or "8" in resp.json()["error"]


@pytest.mark.asyncio
async def test_reset_password_rejects_same_as_current(client):
    resp = await client.post(
        "/reset-password",
        json={"current_password": "Same#pass1", "new_password": "Same#pass1"},
    )
    assert resp.status_code == 400
    assert "different" in resp.json()["error"].lower()
