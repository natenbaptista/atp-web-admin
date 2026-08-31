"""Line Groups API: delete payload type, unique sub-line, appearance suffixes."""

from unittest.mock import AsyncMock

import pytest

import atp_client
from routers.lines import (
    _canonical_line_name,
    _lg_add,
    _lg_delete,
    _validate_nesting,
)


EXISTING_GROUPS = [
    {"main_line": "2402", "sub_lines": ["2406", "2403"]},
    {"main_line": "2404", "sub_lines": ["2405", "2406"]},
]


class FakeRequest:
    def __init__(self, body):
        self.headers = {
            "content-type": "application/json",
            "accept": "application/json",
        }
        self._body = body

    async def json(self):
        return self._body


def test_canonical_line_name_strips_appearance_suffix():
    assert _canonical_line_name("2407--1") == "2407"
    assert _canonical_line_name("2407--2") == "2407"
    assert _canonical_line_name("2407") == "2407"
    assert _canonical_line_name("  2401  ") == "2401"
    assert _canonical_line_name("") == ""


@pytest.mark.asyncio
async def test_line_group_delete_sends_plain_string(monkeypatch, tmp_path):
    monkeypatch.setenv("ATPMGR_DATADIR", str(tmp_path))
    sent = {}

    async def fake_send(msg_type, payload=None):
        sent["type"] = msg_type
        sent["payload"] = payload
        return {"type": "ok", "payload": {}}

    monkeypatch.setattr(atp_client, "_send_message", fake_send)
    await atp_client.line_group_delete("2402")
    assert sent["type"] == "controller_line_group_delete"
    assert sent["payload"] == "2402"
    assert isinstance(sent["payload"], str)


@pytest.mark.asyncio
async def test_delete_handler_uses_body_main_line(monkeypatch):
    deleted = {}

    async def fake_delete(main_line):
        deleted["main_line"] = main_line

    monkeypatch.setattr(atp_client, "line_group_delete", fake_delete)
    resp = await _lg_delete(
        "2402",
        FakeRequest({"main_line": "2402"}),
        {"username": "admin"},
        True,
    )
    assert resp.status_code == 200
    assert deleted["main_line"] == "2402"
    assert resp.body and b"success" in resp.body


@pytest.mark.asyncio
async def test_validate_rejects_sub_already_in_another_group(monkeypatch):
    monkeypatch.setattr(
        atp_client, "line_group_search", AsyncMock(return_value=EXISTING_GROUPS)
    )
    err = await _validate_nesting("2401", ["2406"], editing=False)
    assert err is not None
    assert err.status_code == 422
    assert b"2406" in err.body


@pytest.mark.asyncio
async def test_add_rejects_sub_already_in_another_group(monkeypatch):
    monkeypatch.setattr(
        atp_client, "line_group_search", AsyncMock(return_value=EXISTING_GROUPS)
    )
    create = AsyncMock()
    monkeypatch.setattr(atp_client, "line_group_create", create)
    resp = await _lg_add(
        FakeRequest({"main_line": "2401", "sub_lines": ["2406"]}),
        {"username": "admin"},
        True,
    )
    assert resp.status_code == 422
    assert b"2406" in resp.body
    create.assert_not_called()


@pytest.mark.asyncio
async def test_add_strips_appearance_suffix_before_create(monkeypatch):
    monkeypatch.setattr(atp_client, "line_group_search", AsyncMock(return_value=[]))
    created = {}

    async def fake_create(group):
        created.update(group)

    monkeypatch.setattr(atp_client, "line_group_create", fake_create)
    resp = await _lg_add(
        FakeRequest({"main_line": "2401", "sub_lines": ["2407--1"]}),
        {"username": "admin"},
        True,
    )
    assert resp.status_code == 200
    assert created["sub_lines"] == ["2407"]
    assert created["main_line"] == "2401"
