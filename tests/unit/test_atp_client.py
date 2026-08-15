"""
tests/unit/test_atp_client.py — unit tests for atp_client.py

All tests patch asyncio.open_unix_connection so no ATP daemon is needed.
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, patch

import pytest

import atp_client
from tests.conftest import make_backend_reply, patch_backend


# ── _socket_path ──────────────────────────────────────────────────────────────

def test_socket_path_raises_without_env(monkeypatch):
    monkeypatch.delenv("ATPMGR_DATADIR", raising=False)
    with pytest.raises(atp_client.AtpBackendError, match="ATPMGR_DATADIR"):
        atp_client._socket_path()


def test_socket_path_with_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ATPMGR_DATADIR", str(tmp_path))
    assert atp_client._socket_path() == str(tmp_path / "control")


# ── _extract_list ─────────────────────────────────────────────────────────────

def test_extract_list_from_list():
    assert atp_client._extract_list([1, 2, 3]) == [1, 2, 3]


def test_extract_list_from_dict_known_key():
    assert atp_client._extract_list({"users": [{"username": "alice"}]}) == [{"username": "alice"}]


def test_extract_list_from_dict_fallback_key():
    assert atp_client._extract_list({"results": ["a", "b"]}) == ["a", "b"]


def test_extract_list_from_dict_no_match():
    assert atp_client._extract_list({"unknown": "value"}) == []


def test_extract_list_extra_keys():
    assert atp_client._extract_list({"mykey": [1, 2]}, "mykey") == [1, 2]


# ── _extract_dict ─────────────────────────────────────────────────────────────

def test_extract_dict_from_dict():
    d = {"a": 1}
    assert atp_client._extract_dict(d) == d


def test_extract_dict_from_non_dict():
    assert atp_client._extract_dict([1, 2]) == {}
    assert atp_client._extract_dict(None) == {}


# ── authenticate ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_authenticate_success(monkeypatch, tmp_path):
    monkeypatch.setenv("ATPMGR_DATADIR", str(tmp_path))
    user_data = {"username": "alice", "role": "admin", "guid": "abc123"}
    with patch_backend("node_announce_login_succeeded", user_data):
        result = await atp_client.authenticate("alice", "secret")
    assert result["username"] == "alice"
    assert result["role"] == "admin"


@pytest.mark.asyncio
async def test_authenticate_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("ATPMGR_DATADIR", str(tmp_path))
    with patch_backend("node_announce_login_failed", {"message": "Bad credentials"}):
        with pytest.raises(ValueError, match="Bad credentials"):
            await atp_client.authenticate("alice", "wrong")


# ── user_search ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_search_returns_list(monkeypatch, tmp_path):
    monkeypatch.setenv("ATPMGR_DATADIR", str(tmp_path))
    users = [{"username": "alice"}, {"username": "bob"}]
    with patch_backend("node_announce_ok", users):
        result = await atp_client.user_search("a")
    assert len(result) == 2
    assert result[0]["username"] == "alice"


@pytest.mark.asyncio
async def test_user_search_empty_payload(monkeypatch, tmp_path):
    monkeypatch.setenv("ATPMGR_DATADIR", str(tmp_path))
    with patch_backend("node_announce_ok", []):
        result = await atp_client.user_search("")
    assert result == []


# ── group_search ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_group_search_returns_dict(monkeypatch, tmp_path):
    monkeypatch.setenv("ATPMGR_DATADIR", str(tmp_path))
    groups = [{"name": "sales", "id": "g1"}, {"name": "support", "id": "g2"}]
    with patch_backend("node_announce_ok", groups):
        result = await atp_client.group_search()
    # group_search returns {name: group_data}
    assert "sales" in result
    assert "support" in result


# ── trunk_search ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trunk_search(monkeypatch, tmp_path):
    monkeypatch.setenv("ATPMGR_DATADIR", str(tmp_path))
    trunks = [{"name": "trunk1", "host": "1.2.3.4"}]
    with patch_backend("node_announce_ok", trunks):
        result = await atp_client.trunk_search()
    assert result[0]["name"] == "trunk1"


# ── AtpBackendError on connection failure ─────────────────────────────────────

@pytest.mark.asyncio
async def test_connection_refused_raises_backend_error(monkeypatch, tmp_path):
    monkeypatch.setenv("ATPMGR_DATADIR", str(tmp_path))
    with patch(
        "asyncio.open_unix_connection",
        new=AsyncMock(side_effect=ConnectionRefusedError("refused")),
    ):
        with pytest.raises(atp_client.AtpBackendError, match="Cannot connect"):
            await atp_client.user_search("")


@pytest.mark.asyncio
async def test_timeout_raises_backend_error(monkeypatch, tmp_path):
    monkeypatch.setenv("ATPMGR_DATADIR", str(tmp_path))
    with patch(
        "asyncio.open_unix_connection",
        new=AsyncMock(side_effect=asyncio.TimeoutError()),
    ):
        with pytest.raises(atp_client.AtpBackendError, match="Timed out"):
            await atp_client.user_search("")
