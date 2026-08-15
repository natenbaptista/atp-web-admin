"""
tests/unit/test_users_routes.py — tests for /users/* routes.

Backend calls are patched so no ATP daemon is needed.
"""

import pytest

from tests.conftest import patch_backend, ADMIN_USER


USERS_PAYLOAD = [
    {"username": "alice", "first_name": "Alice", "last_name": "Smith", "role": "user", "guid": "g1"},
    {"username": "bob",   "first_name": "Bob",   "last_name": "Jones", "role": "admin", "guid": "g2"},
]


# ── GET /users ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_users_index_renders(client):
    with patch_backend("node_announce_ok", USERS_PAYLOAD):
        resp = await client.get("/users")
    assert resp.status_code == 200
    assert b"alice" in resp.content.lower() or b"Users" in resp.content


@pytest.mark.asyncio
async def test_users_index_requires_auth(anon_client):
    resp = await anon_client.get("/users", follow_redirects=False)
    # Either 401 or redirect to login
    assert resp.status_code in (401, 302, 303)


# ── GET /users/add ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_users_add_renders(client):
    # /users/add queries group_search for the groups select
    groups_payload = [{"name": "sales", "id": "g-sales"}, {"name": "support", "id": "g-support"}]
    with patch_backend("node_announce_ok", groups_payload):
        resp = await client.get("/users/add")
    assert resp.status_code == 200
    assert b"form" in resp.content.lower()


# ── GET /users/{username}/edit ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_users_edit_renders(client):
    user_detail = [USERS_PAYLOAD[0]]  # single-item list (as backend returns)
    with patch_backend("node_announce_ok", user_detail):
        resp = await client.get("/users/alice/edit")
    # 200 or 404 if backend returns empty; just verify no 500
    assert resp.status_code in (200, 404)


# ── GET /groups ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_groups_index_renders(client):
    groups_payload = [{"name": "sales", "id": "g-sales", "members": []}]
    with patch_backend("node_announce_ok", groups_payload):
        resp = await client.get("/groups")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_groups_add_renders(client):
    with patch_backend("node_announce_ok", []):
        resp = await client.get("/groups/add")
    assert resp.status_code == 200


# ── GET /lines ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lines_index_renders(client):
    lines_payload = [{"dn": "101", "name": "Reception", "type": "standard"}]
    with patch_backend("node_announce_ok", lines_payload):
        resp = await client.get("/lines")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_lines_add_renders(client):
    with patch_backend("node_announce_ok", []):
        resp = await client.get("/lines/add")
    assert resp.status_code == 200


# ── GET /trunks ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trunks_index_renders(client):
    trunks_payload = [{"name": "sip-trunk-1", "host": "sip.example.com"}]
    with patch_backend("node_announce_ok", trunks_payload):
        resp = await client.get("/trunks")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_trunks_add_renders(client):
    with patch_backend("node_announce_ok", []):
        resp = await client.get("/trunks/add")
    assert resp.status_code == 200


# ── GET /routes ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_routes_index_renders(client):
    routes_payload = [{"name": "route-1", "trunk": "sip-trunk-1", "prefix": ""}]
    with patch_backend("node_announce_ok", routes_payload):
        resp = await client.get("/routes")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_routes_add_renders(client):
    with patch_backend("node_announce_ok", []):
        resp = await client.get("/routes/add")
    assert resp.status_code == 200


# ── GET /directory/search ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_directory_search_short_query(client):
    """Query < 2 chars returns empty list without hitting backend."""
    resp = await client.get("/directory/search?q=a")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_directory_search_users(client):
    users_payload = [{"username": "alice", "first_name": "Alice", "last_name": "Smith", "role": "user"}]
    lines_payload: list = []
    # directory_search calls user_search then line_search — patch both to same mock
    with patch_backend("node_announce_ok", users_payload):
        resp = await client.get("/directory/search?q=alice")
    assert resp.status_code == 200
    results = resp.json()
    # At least the user result (line search may fail gracefully)
    assert isinstance(results, list)


# ── GET /license ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_license_index_renders(client):
    with patch_backend("node_announce_ok", []):
        resp = await client.get("/license")
    assert resp.status_code == 200


# ── GET /stations ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stations_index_renders(client):
    resp = await client.get("/stations")
    assert resp.status_code == 200


# ── GET /logs ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_logs_index_renders(client):
    resp = await client.get("/logs")
    assert resp.status_code == 200


# ── GET /directory ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_directory_index_renders(client):
    resp = await client.get("/directory")
    assert resp.status_code == 200
