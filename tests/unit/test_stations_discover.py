"""Stations discover: generic missing-software must not look like a broken install."""

import pytest

from routers import stations as stations_mod
from routers.stations import (
    classify_discover_result,
    is_generic_missing_software,
    missing_software_detail,
)


GENERIC = "Node install has missing software(s)!"


def test_missing_software_detail_empty_for_canned_banner():
    assert missing_software_detail(GENERIC) == ""
    assert missing_software_detail(GENERIC + "\n") == ""
    assert missing_software_detail("") == ""


def test_missing_software_detail_keeps_named_packages():
    assert missing_software_detail(GENERIC + " nmap sshpass") == "nmap sshpass"
    assert missing_software_detail("nmap, sshpass") == ""


def test_is_generic_missing_software():
    assert is_generic_missing_software(GENERIC) is True
    assert is_generic_missing_software("node install has missing software(s)") is True
    assert is_generic_missing_software(GENERIC + " nmap") is False
    assert is_generic_missing_software("Permission denied") is False
    assert is_generic_missing_software("") is False


def test_classify_generic_error_file_is_empty_success():
    result = classify_discover_result(GENERIC, "", [], False)
    assert result["success"] is True
    assert result["stations"] == []
    assert result["output"] == ""


def test_classify_generic_in_stdout_is_empty_success():
    result = classify_discover_result("", GENERIC, [], False)
    assert result["success"] is True
    assert result["stations"] == []
    assert result["output"] == ""


def test_classify_named_missing_software_is_failure():
    result = classify_discover_result(GENERIC + "\nnmap sshpass", "", [], False)
    assert result["success"] is False
    assert result["stations"] == []
    assert "nmap" in result["output"]
    assert "sshpass" in result["output"]
    assert "missing software(s)" not in result["output"]


def test_classify_stations_win_over_generic_error():
    stations = [{"id": "10.0.0.8", "station": "10.0.0.8", "name": "s1", "user": "No User"}]
    result = classify_discover_result(GENERIC, "", stations, False)
    assert result["success"] is True
    assert result["stations"] == stations


def test_classify_real_error_stays_failure():
    result = classify_discover_result("IP 10.0.0.8 is not reachable!", "", [], False)
    assert result["success"] is False
    assert result["output"] == "IP 10.0.0.8 is not reachable!"


def test_classify_installer_ok_empty_is_success():
    result = classify_discover_result("", "", [], True)
    assert result["success"] is True
    assert result["stations"] == []


def test_classify_installer_fail_with_other_output():
    result = classify_discover_result("", "Permission denied", [], False)
    assert result["success"] is False
    assert result["output"] == "Permission denied"


def test_classify_generic_error_does_not_eat_real_stdout_failure():
    result = classify_discover_result(GENERIC, "Permission denied", [], False)
    assert result["success"] is False
    assert result["output"] == "Permission denied"


def test_classify_generic_error_plus_ok_stdout_is_empty_success():
    result = classify_discover_result(GENERIC, "scanning lan", [], True)
    assert result["success"] is True
    assert result["output"] == ""


def test_parse_stationslist(tmp_path, monkeypatch):
    path = tmp_path / "stationslist"
    path.write_text("10.0.0.8,front,alice\n10.0.0.9,back,\n\n")
    monkeypatch.setattr(stations_mod, "_STATIONSLIST", path)
    parsed = stations_mod._parse_stationslist()
    assert parsed[0]["station"] == "10.0.0.8"
    assert parsed[0]["name"] == "front"
    assert parsed[0]["user"] == "alice"
    assert parsed[1]["user"] == "No User"


async def _post_discover(client, tmp_path, monkeypatch, *, err_text, list_text="", installer_ok=False, output=""):
    fake_bin = tmp_path / "amp_station_installer"
    fake_bin.write_text("#!/bin/sh\nexit 0\n")
    fake_bin.chmod(0o755)
    errors = tmp_path / "errors"
    stationslist = tmp_path / "stationslist"

    async def fake_run(args, timeout=60):
        if err_text is not None:
            errors.write_text(err_text)
        if list_text:
            stationslist.write_text(list_text)
        return installer_ok, output

    monkeypatch.setattr(stations_mod, "_installer_binary", lambda: str(fake_bin))
    monkeypatch.setattr(stations_mod, "_ERRORS_FILE", errors)
    monkeypatch.setattr(stations_mod, "_STATIONSLIST", stationslist)
    monkeypatch.setattr(stations_mod, "_run_installer", fake_run)
    return await client.post("/stations/discover")


@pytest.mark.asyncio
async def test_discover_generic_errors_file_returns_empty_success(client, tmp_path, monkeypatch):
    resp = await _post_discover(client, tmp_path, monkeypatch, err_text=GENERIC)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["stations"] == []
    assert data["output"] == ""


@pytest.mark.asyncio
async def test_discover_no_stations_no_errors_is_empty_success(client, tmp_path, monkeypatch):
    resp = await _post_discover(
        client, tmp_path, monkeypatch, err_text=None, installer_ok=True
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["stations"] == []
    assert "missing software" not in data["output"].lower()


@pytest.mark.asyncio
async def test_discover_named_missing_software_names_packages(client, tmp_path, monkeypatch):
    resp = await _post_discover(
        client, tmp_path, monkeypatch, err_text=GENERIC + " nmap"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert "nmap" in data["output"]
    assert data["stations"] == []


@pytest.mark.asyncio
async def test_discover_stationslist_returned_despite_generic_error(client, tmp_path, monkeypatch):
    resp = await _post_discover(
        client,
        tmp_path,
        monkeypatch,
        err_text=GENERIC,
        list_text="192.168.1.10,desk,bob\n",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["stations"][0]["station"] == "192.168.1.10"


@pytest.mark.asyncio
async def test_discover_binary_missing_is_empty_success(client, tmp_path, monkeypatch):
    missing = tmp_path / "no-such-installer"
    monkeypatch.setattr(stations_mod, "_installer_binary", lambda: str(missing))
    resp = await client.post("/stations/discover")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["stations"] == []


@pytest.mark.asyncio
async def test_discover_requires_auth(anon_client):
    resp = await anon_client.post("/stations/discover", follow_redirects=False)
    assert resp.status_code in (401, 302, 303, 307, 308)


@pytest.mark.asyncio
async def test_stations_page_loads_overlay_and_other_overlays(client):
    resp = await client.get("/stations")
    assert resp.status_code == 200
    html = resp.text
    assert "/stations.js" in html
    assert "/line-groups.js" in html
    assert "/password-security.js" in html
    assert "/gd-spa-layout.js" in html
    assert "index-CgQw_K3l.js" in html
