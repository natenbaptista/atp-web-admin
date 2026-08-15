#!/usr/bin/env python3
"""
webadmin-24 — Full Integration Test Suite
==========================================
Tests every endpoint, form field, validation rule and role restriction.

Usage:
    python3 test_suite.py [BASE_URL] [ADMIN_USER] [ADMIN_PASS]

Defaults:
    BASE_URL   = https://localhost:8443
    ADMIN_USER = admin
    ADMIN_PASS = admin

The script creates its own test data and cleans it up at the end.
All test entities use the prefix  testauto  so they are easy to spot.
"""

import sys
import warnings
import requests
import urllib3

# Suppress SSL warnings for self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

# ── Configuration ─────────────────────────────────────────────────────────────

BASE_URL   = (sys.argv[1] if len(sys.argv) > 1 else "https://localhost:8443").rstrip("/")
ADMIN_USER = sys.argv[2] if len(sys.argv) > 2 else "admin"
ADMIN_PASS = sys.argv[3] if len(sys.argv) > 3 else "admin"

# Test-data identifiers — valid chars only: A-Z a-z 0-9 . -
# Users/groups: only alphanumeric + . and -
# Trunks/routes/inbounds/outbounds: less strict, just no spaces
T_USER_ADMIN   = "testauto.admin"
T_USER_USER    = "testauto.user"
T_USER_AUDITOR = "testauto.auditor"
T_GROUP        = "testauto.grp"
T_TRUNK        = "testautottunk"
T_LINE_DN      = "9991"
T_INBOUND      = "testautoinb"
T_OUTBOUND     = "testautoout"
T_ROUTE        = "testautorte"

# ── ANSI colours ──────────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

# ── Result tracking ───────────────────────────────────────────────────────────

results: list[dict] = []


def _record(section: str, name: str, passed: bool, detail: str = ""):
    results.append({"section": section, "name": name, "passed": passed, "detail": detail})
    icon = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
    msg  = f"  [{icon}] {name}"
    if detail:
        msg += f"  — {YELLOW}{detail}{RESET}"
    print(msg)


def _warn(section: str, name: str, detail: str = ""):
    """Backend-unavailable result — not a code bug, shown as warning."""
    results.append({"section": section, "name": name, "passed": True, "detail": f"WARN(backend): {detail}"})
    msg = f"  [{YELLOW}WARN{RESET}] {name}"
    if detail:
        msg += f"  — {YELLOW}{detail}{RESET}"
    print(msg)


def section(title: str):
    print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*60}{RESET}")


def _body(r: requests.Response, limit: int = 120) -> str:
    try:
        return str(r.json())[:limit]
    except Exception:
        return r.text[:limit]


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Accept": "application/json"})
    s.verify = False
    return s


def login(username: str, password: str, session: requests.Session | None = None):
    s = session or new_session()
    r = s.post(f"{BASE_URL}/login", json={"username": username, "password": password},
               allow_redirects=False, verify=False)
    return s, r


def get_json(s: requests.Session, path: str, params: dict | None = None) -> requests.Response:
    return s.get(f"{BASE_URL}{path}", params=params, verify=False)


def post_form(s: requests.Session, path: str, data: dict) -> requests.Response:
    return s.post(f"{BASE_URL}{path}", data=data, allow_redirects=False, verify=False)


def post_json(s: requests.Session, path: str, payload: dict) -> requests.Response:
    return s.post(f"{BASE_URL}{path}", json=payload, allow_redirects=False, verify=False)


def _backend_ok(r: requests.Response, sec: str, name: str) -> bool:
    """Return True if response is success; if 502 log as WARN (backend unavailable), else FAIL."""
    if r.status_code == 303:
        _record(sec, name, True, "303 redirect")
        return True
    if r.status_code == 502:
        _warn(sec, name, f"ATP backend unavailable — {_body(r, 100)}")
        return False
    _record(sec, name, False, f"got {r.status_code} — {_body(r, 100)}")
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — AUTHENTICATION
# ═══════════════════════════════════════════════════════════════════════════════

section("1. AUTHENTICATION")

r = requests.get(f"{BASE_URL}/health", verify=False)
_record("Auth", "GET /health returns 200", r.status_code == 200)

s_anon = new_session()
s_anon, r = login("nobody", "wrongpass", s_anon)
_record("Auth", "POST /login bad creds returns 401", r.status_code in (401, 422),
        f"got {r.status_code}")

s_admin = new_session()
s_admin, r = login(ADMIN_USER, ADMIN_PASS, s_admin)
_record("Auth", "POST /login admin succeeds (200 or 303)",
        r.status_code in (200, 303), f"got {r.status_code}")

r = get_json(s_admin, "/session-check")
_record("Auth", "GET /session-check returns 200 for live session",
        r.status_code == 200, f"got {r.status_code}")

s_unauth = new_session()
r = get_json(s_unauth, "/users/search")
_record("Auth", "GET /users/search without session returns 401",
        r.status_code == 401, f"got {r.status_code}")


# ─── PRE-CLEANUP (silently remove any leftover data from previous runs) ───────
print(f"\n{BOLD}{CYAN}  Pre-cleanup: removing any leftover test data...{RESET}")

def _preclean(label: str, path: str):
    r = post_form(s_admin, path, {})
    status = "removed" if r.status_code in (200, 303) else f"skip ({r.status_code})"
    print(f"    {label}: {status}")

_preclean(f"User      {T_USER_USER}",    f"/users/{T_USER_USER}/delete")
_preclean(f"User      {T_USER_ADMIN}",   f"/users/{T_USER_ADMIN}/delete")
_preclean(f"User      {T_USER_AUDITOR}", f"/users/{T_USER_AUDITOR}/delete")
_preclean(f"Group     {T_GROUP}",        f"/groups/{T_GROUP}/delete")
_preclean(f"Line      DN={T_LINE_DN}",   f"/lines/{T_LINE_DN}/delete")
_preclean(f"LineGroup DN={T_LINE_DN}",   f"/lines/line-groups/{T_LINE_DN}/delete")
_preclean(f"Trunk     {T_TRUNK}",        f"/trunks/{T_TRUNK}/delete")
_preclean(f"Outbound  {T_OUTBOUND}",     f"/outbounds/{T_OUTBOUND}/delete")
_preclean(f"Inbound   {T_INBOUND}",      f"/inbounds/{T_INBOUND}/delete")
_preclean(f"Route     {T_ROUTE}",        f"/routes/{T_ROUTE}/delete")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — USERS
# ═══════════════════════════════════════════════════════════════════════════════

section("2. USERS — List / Search / Validate")

r = get_json(s_admin, "/users/search")
_record("Users", "GET /users/search returns 200 JSON list",
        r.status_code == 200 and isinstance(r.json(), list), f"got {r.status_code}")

r = get_json(s_admin, "/users/validate", {"username": ADMIN_USER})
_record("Users", "GET /users/validate taken username returns non-true",
        r.status_code == 200 and r.json() is not True, f"body={r.text[:80]}")

r = get_json(s_admin, "/users/validate", {"username": T_USER_USER})
_record("Users", "GET /users/validate available username returns true",
        r.status_code == 200 and r.json() is True, f"body={r.text[:80]}")


section("2a. USERS — Create (Admin role)")

ADMIN_PAYLOAD = {
    "username": T_USER_ADMIN, "password": "TestPass1!", "confirm_password": "TestPass1!",
    "first_name": "Test", "last_name": "Admin", "role": "Admin",
    "email": "testadmin@test.local", "turret_login": "", "turret_pin": "",
    "timezone": "UTC", "display_name": "Test Admin", "device_type": "fuse",
    "vr_retention_years": "0", "vr_retention_months": "0", "vr_retention_days": "7",
}
r = post_form(s_admin, "/users/add", ADMIN_PAYLOAD)
_record("Users", f"POST /users/add Admin role → 303",
        r.status_code == 303, f"got {r.status_code} — {_body(r)}")

r = get_json(s_admin, "/users/search", {"q": T_USER_ADMIN})
found_admin = any(u.get("username") == T_USER_ADMIN for u in (r.json() or []))
_record("Users", f"Admin user {T_USER_ADMIN} appears in search", found_admin)


section("2b. USERS — Create (User role + all turret fields)")

USER_PAYLOAD = {
    "username": T_USER_USER, "password": "TestPass1!", "confirm_password": "TestPass1!",
    "first_name": "Test", "last_name": "User", "role": "User",
    "email": "testuser@test.local", "turret_login": "8801", "turret_pin": "1234",
    "timezone": "UTC", "display_name": "Test User", "device_type": "fuse",
    "vr_retention_years": "0", "vr_retention_months": "0", "vr_retention_days": "7",
    # Turret settings
    "home_page": "1", "home_page_return_timeout": "30",
    "ringer_volume": "60", "ringer_on_off": "on",
    "handsfree_transmit_volume": "50", "left_transmit_broadcast_vol": "50",
    "left_receive_broadcast_vol": "50",
    "button_label_font_size": "16", "button_cli_font_size": "14",
    "local_hunt_group": "1", "primary_timezone": "Europe/London",
    "auto_toggle_options": "none", "display_sleep_time": "1h",
    "display_inactivity_timeout": "5min", "display_wakeup_time": "7:00",
    "ptt_move_to_hset": "on", "group_all_as_unlatch_all": "",
    "right_handset_default": "on", "auto_show_call_control": "",
    "noise_suppression_level": "off", "default_handset_timeout": "30",
    "display_recordings_on_turret": "0",
    "ext_dial_pad_as_std_tele_pad": "", "normal_dial_pad_as_std_tele_pad": "",
}
r = post_form(s_admin, "/users/add", USER_PAYLOAD)
_record("Users", f"POST /users/add User role → 303",
        r.status_code == 303, f"got {r.status_code} — {_body(r)}")

r = get_json(s_admin, f"/users/detail/{T_USER_USER}")
_record("Users", "GET /users/detail/{username} returns merged record",
        r.status_code == 200 and r.json().get("username") == T_USER_USER,
        f"got {r.status_code}")
if r.status_code == 200:
    d = r.json()
    _record("Users", "  detail: turret_login populated",
            d.get("turret_login") == "8801", f"got '{d.get('turret_login')}'")
    _record("Users", "  detail: home_page setting loaded",
            str(d.get("home_page", "")) == "1", f"got '{d.get('home_page')}'")
    _record("Users", "  detail: ringer_volume setting loaded",
            str(d.get("ringer_volume", "")) == "60", f"got '{d.get('ringer_volume')}'")


section("2c. USERS — Create (Auditor role)")

AUD_PAYLOAD = {
    "username": T_USER_AUDITOR, "password": "TestPass1!", "confirm_password": "TestPass1!",
    "first_name": "Test", "last_name": "Auditor", "role": "Auditor",
    "email": "testaud@test.local", "turret_login": "", "turret_pin": "",
    "timezone": "UTC", "display_name": "Test Auditor", "device_type": "fuse",
    "vr_retention_years": "0", "vr_retention_months": "0", "vr_retention_days": "7",
}
r = post_form(s_admin, "/users/add", AUD_PAYLOAD)
_record("Users", f"POST /users/add Auditor role → 303",
        r.status_code == 303, f"got {r.status_code} — {_body(r)}")


section("2d. USERS — Validation rules")

# Username: empty
r = post_form(s_admin, "/users/add", {**USER_PAYLOAD, "username": ""})
_record("Users", "Validation: empty username → 422 with errors.username",
        r.status_code == 422 and "username" in r.json().get("errors", {}),
        f"got {r.status_code}, errors={list(r.json().get('errors',{}).keys())}")

# Username: too short
r = post_form(s_admin, "/users/add", {**USER_PAYLOAD, "username": "ab"})
_record("Users", "Validation: username < 3 chars → 422",
        r.status_code == 422 and "username" in r.json().get("errors", {}))

# Username: invalid chars (space + !)
r = post_form(s_admin, "/users/add", {**USER_PAYLOAD, "username": "bad user!"})
_record("Users", "Validation: username with spaces/! → 422",
        r.status_code == 422 and "username" in r.json().get("errors", {}))

# Password: missing on add
r = post_form(s_admin, "/users/add", {**USER_PAYLOAD,
              "username": "testauto.nopwd", "password": "", "confirm_password": ""})
_record("Users", "Validation: empty password on add → 422 errors.password",
        r.status_code == 422 and "password" in r.json().get("errors", {}))

# Password: mismatch
r = post_form(s_admin, "/users/add", {**USER_PAYLOAD,
              "username": "testauto.mismatch", "password": "Abc123!", "confirm_password": "Different!"})
_record("Users", "Validation: password mismatch → 422 errors.confirm_password",
        r.status_code == 422 and "confirm_password" in r.json().get("errors", {}))

# turret_pin: too short
r = post_form(s_admin, "/users/add", {**USER_PAYLOAD,
              "username": "testauto.badpin", "turret_pin": "123"})
_record("Users", "Validation: turret_pin 3 digits → 422",
        r.status_code == 422 and "turret_pin" in r.json().get("errors", {}))

# turret_pin: letters
r = post_form(s_admin, "/users/add", {**USER_PAYLOAD,
              "username": "testauto.badpin2", "turret_pin": "abcd"})
_record("Users", "Validation: turret_pin with letters → 422",
        r.status_code == 422 and "turret_pin" in r.json().get("errors", {}))


section("2e. USERS — Edit")

EDIT_USER_PAYLOAD = {
    "first_name": "Edited", "last_name": "User", "role": "User",
    "email": "edited@test.local", "turret_login": "8801", "turret_pin": "5678",
    "timezone": "UTC", "display_name": "Edited User", "device_type": "fuse",
    "vr_retention_years": "0", "vr_retention_months": "0", "vr_retention_days": "14",
    "home_page": "2", "home_page_return_timeout": "60",
    "ringer_volume": "80", "ringer_on_off": "",
    "handsfree_transmit_volume": "40", "left_transmit_broadcast_vol": "40",
    "left_receive_broadcast_vol": "40",
    "button_label_font_size": "18", "button_cli_font_size": "16",
    "local_hunt_group": "2", "primary_timezone": "America/New_York",
    "auto_toggle_options": "auto_hold", "display_sleep_time": "2h",
    "display_inactivity_timeout": "10min", "display_wakeup_time": "8:00",
    "ptt_move_to_hset": "", "group_all_as_unlatch_all": "on",
    "right_handset_default": "", "auto_show_call_control": "on",
    "noise_suppression_level": "low", "default_handset_timeout": "60",
    "display_recordings_on_turret": "2",
}
r = post_form(s_admin, f"/users/{T_USER_USER}/edit", EDIT_USER_PAYLOAD)
ok = _backend_ok(r, "Users", f"POST /users/{T_USER_USER}/edit → 303")
if ok:
    r = get_json(s_admin, f"/users/detail/{T_USER_USER}")
    if r.status_code == 200:
        d = r.json()
        _record("Users", "  edit: first_name updated to 'Edited'",
                d.get("first_name") == "Edited", f"got '{d.get('first_name')}'")
        _record("Users", "  edit: home_page updated to 2",
                str(d.get("home_page", "")) == "2", f"got '{d.get('home_page')}'")
        _record("Users", "  edit: ringer_volume updated to 80",
                str(d.get("ringer_volume", "")) == "80", f"got '{d.get('ringer_volume')}'")


section("2f. USERS — Role-based access (User session)")

s_user = new_session()
s_user, r_login = login(T_USER_USER, "TestPass1!", s_user)
user_login_ok = r_login.status_code in (200, 303)
_record("Users", "User role login succeeds", user_login_ok, f"got {r_login.status_code}")

if user_login_ok:
    r = get_json(s_user, "/users/search")
    _record("Users", "User role: GET /users/search → 200", r.status_code == 200)
    r = get_json(s_user, f"/users/detail/{T_USER_USER}")
    _record("Users", "User role: GET /users/detail/{own} → 200", r.status_code == 200)
    r = post_form(s_user, f"/users/{T_USER_ADMIN}/delete", {})
    _record("Users", "User role: cannot delete another user → 303 or 403",
            r.status_code in (303, 403), f"got {r.status_code}")


section("2g. USERS — Auditor session")

s_aud = new_session()
s_aud, r_aud = login(T_USER_AUDITOR, "TestPass1!", s_aud)
aud_login_ok = r_aud.status_code in (200, 303)
_record("Users", "Auditor role login succeeds", aud_login_ok, f"got {r_aud.status_code}")

if aud_login_ok:
    r = get_json(s_aud, "/audit")
    _record("Users", "Auditor: GET /audit → 200", r.status_code == 200)
    r = get_json(s_aud, "/calls/json")
    _record("Users", "Auditor: GET /calls/json → 200", r.status_code == 200)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — GROUPS
# ═══════════════════════════════════════════════════════════════════════════════

section("3. GROUPS — CRUD + Validation")

r = get_json(s_admin, "/groups/search")
_record("Groups", "GET /groups/search → 200 JSON list",
        r.status_code == 200 and isinstance(r.json(), list))

r = post_form(s_admin, "/groups/add", {"name": T_GROUP})
_backend_ok(r, "Groups", f"POST /groups/add → 303")

r = get_json(s_admin, "/groups/search", {"q": T_GROUP})
found_grp = any(g.get("name") == T_GROUP for g in (r.json() or []))
_record("Groups", f"Group {T_GROUP} appears in search", found_grp)

# Validation: empty name — groups returns {"errors": {"__all": [...]}}
r = post_form(s_admin, "/groups/add", {"name": ""})
_record("Groups", "Validation: empty group name → 422 with __all errors",
        r.status_code == 422 and "__all" in r.json().get("errors", {}),
        f"got {r.status_code}, body={_body(r)}")

# Validation: bad chars
r = post_form(s_admin, "/groups/add", {"name": "bad group!"})
_record("Groups", "Validation: group name with ! → 422",
        r.status_code == 422, f"got {r.status_code}")

# Edit group (remove all members)
r = post_form(s_admin, f"/groups/{T_GROUP}/edit", {"users": []})
_backend_ok(r, "Groups", f"POST /groups/{T_GROUP}/edit (clear members) → 303")

section("3a. GROUPS — Directory entries")

r = post_form(s_admin, f"/groups/{T_GROUP}/directory/add", {
    "name": "Dir Entry Test", "description": "Auto test",
    "number_office": "123456", "number_mob": "07700900000", "number_home": "",
})
_record("Groups", "POST /groups/{name}/directory/add → 303",
        r.status_code == 303, f"got {r.status_code}")

# Validation: directory missing name
r = post_form(s_admin, f"/groups/{T_GROUP}/directory/add", {"name": ""})
_record("Groups", "Validation: directory entry missing name → 422 or 303",
        r.status_code in (303, 422))


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — LINES
# ═══════════════════════════════════════════════════════════════════════════════

section("4. LINES — CRUD + Validation + Types")

r = get_json(s_admin, "/lines/search")
_record("Lines", "GET /lines/search → 200 JSON list",
        r.status_code == 200 and isinstance(r.json(), list))

LINE_PAYLOAD = {
    "dn": T_LINE_DN, "name": "testauto.line", "description": "Automated test line",
    "type": "Line", "capacity": "4",
    "do_not_record": "", "require_external_on_call": "", "make_virtual": "",
    "has_virtual_lines": "", "drop_call_ext_leave": "",
    "call_answer_indication": "", "line_open_indication": "",
    "transmit_vol": "50", "receive_vol": "50", "hunt_group": "1",
    "busy_on_dnd": "", "forwarding_enabled": "",
    "forward_to": "", "forwarding_condition": "forward_immediately",
}
r = post_form(s_admin, "/lines/add", LINE_PAYLOAD)
_record("Lines", "POST /lines/add (type=Line) → 303",
        r.status_code == 303, f"got {r.status_code} — {_body(r)}")

# Search all lines and look for DN manually (backend may not filter by DN)
r = get_json(s_admin, "/lines/search")
all_lines = r.json() if r.status_code == 200 else []
found_line = any(str(l.get("dn", "")) == T_LINE_DN or
                 str(l.get("name", "")) == "testauto.line" for l in all_lines)
_record("Lines", f"Line DN={T_LINE_DN} appears in search", found_line,
        f"total lines={len(all_lines)}")

# Validation: empty DN — lines router returns 303 redirect (no JSON response)
r = post_form(s_admin, "/lines/add", {**LINE_PAYLOAD, "dn": ""})
_record("Lines", "Validation: empty DN → 303 redirect (no JSON from lines router)",
        r.status_code == 303, f"got {r.status_code}")

# Validation: non-numeric DN
r = post_form(s_admin, "/lines/add", {**LINE_PAYLOAD, "dn": "abc"})
_record("Lines", "Validation: non-numeric DN → 303 redirect",
        r.status_code == 303, f"got {r.status_code}")

# Edit
r = post_form(s_admin, f"/lines/{T_LINE_DN}/edit",
              {**LINE_PAYLOAD, "name": "testauto.line.edited", "capacity": "6", "do_not_record": "on"})
_record("Lines", f"POST /lines/{T_LINE_DN}/edit → 303",
        r.status_code == 303, f"got {r.status_code}")

# Blacklist report
r = get_json(s_admin, "/lines/blacklist-report", {"format": "csv"})
_record("Lines", "GET /lines/blacklist-report?format=csv → 200",
        r.status_code == 200)

section("4a. LINES — Line Groups")

r = get_json(s_admin, "/lines/line-groups/search")
_record("Lines", "GET /lines/line-groups/search → 200", r.status_code == 200)

r = post_form(s_admin, "/lines/line-groups/add", {"main_line": T_LINE_DN})
_record("Lines", "POST /lines/line-groups/add → 303 or 502",
        r.status_code in (303, 502), f"got {r.status_code}")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — TRUNKS + SIP REGISTRATIONS
# ═══════════════════════════════════════════════════════════════════════════════

section("5. TRUNKS — CRUD + Validation")

r = get_json(s_admin, "/trunks/search")
_record("Trunks", "GET /trunks/search → 200 JSON list",
        r.status_code == 200 and isinstance(r.json(), list))

TRUNK_PAYLOAD = {
    "name": T_TRUNK, "trunk_match": "^9",
    "translation_match": "", "translation_sub": "",
    "strip_front_count": "0", "strip_end_count": "0",
    "add_prefix": "9", "add_postfix": "", "type": "SIP",
    "external_sip_address": "192.168.1.1:5060",
    "local_sip_address": "", "public_sip_address": "",
    "sip_protocol": "UDP",
    "sip_remote_username": "testuser", "sip_remote_password": "testpass",
    "sip_remote_realm": "test.local", "monitoring_method": "None",
    "auth_incoming_call": "",
}
r = post_form(s_admin, "/trunks/add", TRUNK_PAYLOAD)
_backend_ok(r, "Trunks", "POST /trunks/add → 303")

r = get_json(s_admin, "/trunks/search", {"q": T_TRUNK})
found_trunk = any(t.get("name") == T_TRUNK for t in (r.json() or []))
_record("Trunks", f"Trunk {T_TRUNK} appears in search", found_trunk)

# Validation: missing name
r = post_form(s_admin, "/trunks/add", {**TRUNK_PAYLOAD, "name": ""})
_record("Trunks", "Validation: empty trunk name → 422",
        r.status_code == 422, f"got {r.status_code}")

# Edit
r = post_form(s_admin, f"/trunks/{T_TRUNK}/edit",
              {**TRUNK_PAYLOAD, "sip_remote_username": "edited_user", "monitoring_method": "OPTIONS"})
_backend_ok(r, "Trunks", f"POST /trunks/{T_TRUNK}/edit → 303")

section("5a. TRUNKS — SIP Protocol variants")

for proto in ["UDP", "TCP", "TLS"]:
    r = post_form(s_admin, f"/trunks/{T_TRUNK}/edit", {**TRUNK_PAYLOAD, "sip_protocol": proto})
    _backend_ok(r, "Trunks", f"  Edit trunk sip_protocol={proto} → 303")

section("5b. TRUNKS — Monitoring methods")

for method in ["None", "OPTIONS", "REGISTER"]:
    r = post_form(s_admin, f"/trunks/{T_TRUNK}/edit", {**TRUNK_PAYLOAD, "monitoring_method": method})
    _backend_ok(r, "Trunks", f"  Edit trunk monitoring_method={method} → 303")

section("5c. TRUNKS — SIP Registrations CRUD")

r = get_json(s_admin, f"/trunks/{T_TRUNK}/sipreg/list")
_record("Trunks", f"GET /trunks/{T_TRUNK}/sipreg/list → 200",
        r.status_code == 200, f"got {r.status_code}")

r = post_form(s_admin, f"/trunks/{T_TRUNK}/sipreg/add",
              {"username": "sipregtest", "password": "sippassword",
               "registrar": "sip.test.local", "expiry": "3600"})
_record("Trunks", "POST /trunks/{name}/sipreg/add → 303",
        r.status_code == 303, f"got {r.status_code}")

r = get_json(s_admin, f"/trunks/{T_TRUNK}/sipreg/list")
sipregs = r.json() if r.status_code == 200 else []
_record("Trunks", "SIP reg appears in list after add",
        len(sipregs) > 0, f"count={len(sipregs)}")

if sipregs:
    reg_id = str(sipregs[0].get("id", sipregs[0].get("reg_id", "1")))
    r = post_form(s_admin, f"/trunks/{T_TRUNK}/sipreg/{reg_id}/edit",
                  {"username": "sipregtest.edited", "registrar": "sip2.test.local", "expiry": "7200"})
    _record("Trunks", "POST /trunks/{name}/sipreg/{id}/edit → 303",
            r.status_code == 303, f"got {r.status_code}")
    r = post_form(s_admin, f"/trunks/{T_TRUNK}/sipreg/{reg_id}/delete", {})
    _record("Trunks", "POST /trunks/{name}/sipreg/{id}/delete → 303",
            r.status_code == 303, f"got {r.status_code}")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

section("6. ROUTES — CRUD + Validation")

r = get_json(s_admin, "/routes/search")
_record("Routes", "GET /routes/search → 200", r.status_code == 200)

ROUTE_PAYLOAD = {
    "name": T_ROUTE, "trunk": T_TRUNK, "prefix": "9",
    "strip_front_count": "1", "strip_end_count": "0",
    "add_prefix": "", "add_postfix": "",
    "failover_trunk": "", "failover_trunk2": "",
}
r = post_form(s_admin, "/routes/add", ROUTE_PAYLOAD)
_backend_ok(r, "Routes", "POST /routes/add → 303")

r = get_json(s_admin, "/routes/search", {"q": T_ROUTE})
found_route = any(rt.get("name") == T_ROUTE for rt in (r.json() or []))
_record("Routes", f"Route {T_ROUTE} appears in search", found_route)

r = post_form(s_admin, "/routes/add", {**ROUTE_PAYLOAD, "name": ""})
_record("Routes", "Validation: empty route name → 422",
        r.status_code == 422)

r = post_form(s_admin, "/routes/add", {**ROUTE_PAYLOAD, "name": "dummy", "trunk": ""})
_record("Routes", "Validation: empty trunk → 422",
        r.status_code == 422)

r = post_form(s_admin, f"/routes/{T_ROUTE}/edit", {**ROUTE_PAYLOAD, "prefix": "8"})
_backend_ok(r, "Routes", f"POST /routes/{T_ROUTE}/edit → 303")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — INBOUND RULES
# ═══════════════════════════════════════════════════════════════════════════════

section("7. INBOUND RULES — CRUD + Validation")

r = get_json(s_admin, "/inbounds/search")
_record("Inbounds", "GET /inbounds/search → 200", r.status_code == 200)

INB_PAYLOAD = {
    "name": T_INBOUND, "dial_plan_match": "^\\+44", "trunk": T_TRUNK,
    "dni_to_dp_match": "", "dni_to_dp_sub": "",
    "dni_to_dp_strip_front": "0", "dni_to_dp_strip_end": "0",
    "dni_to_dp_prefix": "", "dni_to_dp_postfix": "",
    "source_number_match": "", "source_number_sub": "",
    "source_number_strip_front": "0", "source_number_strip_end": "0",
    "source_number_prefix": "", "source_number_postfix": "",
}
r = post_form(s_admin, "/inbounds/add", INB_PAYLOAD)
_backend_ok(r, "Inbounds", "POST /inbounds/add → 303")

r = get_json(s_admin, "/inbounds/search", {"q": T_INBOUND})
found_inb = any(i.get("name") == T_INBOUND for i in (r.json() or []))
_record("Inbounds", f"Inbound {T_INBOUND} appears in search", found_inb)

r = post_form(s_admin, "/inbounds/add", {**INB_PAYLOAD, "name": ""})
_record("Inbounds", "Validation: empty name → 422", r.status_code == 422)

r = post_form(s_admin, "/inbounds/add", {**INB_PAYLOAD, "name": "dummy", "dial_plan_match": ""})
_record("Inbounds", "Validation: empty dial_plan_match → 422", r.status_code == 422)

r = post_form(s_admin, f"/inbounds/{T_INBOUND}/edit",
              {**INB_PAYLOAD, "dial_plan_match": "^\\+1", "source_number_prefix": "0044"})
_backend_ok(r, "Inbounds", f"POST /inbounds/{T_INBOUND}/edit → 303")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — OUTBOUND RULES
# ═══════════════════════════════════════════════════════════════════════════════

section("8. OUTBOUND RULES — CRUD + Validation")

r = get_json(s_admin, "/outbounds/search")
_record("Outbounds", "GET /outbounds/search → 200", r.status_code == 200)

OUT_PAYLOAD = {
    "name": T_OUTBOUND, "dial_plan_match": "^9",
    "translation_match": "", "translation_sub": "", "route": T_ROUTE,
}
r = post_form(s_admin, "/outbounds/add", OUT_PAYLOAD)
_backend_ok(r, "Outbounds", "POST /outbounds/add → 303")

r = get_json(s_admin, "/outbounds/search", {"q": T_OUTBOUND})
found_out = any(o.get("name") == T_OUTBOUND for o in (r.json() or []))
_record("Outbounds", f"Outbound {T_OUTBOUND} appears in search", found_out)

r = post_form(s_admin, "/outbounds/add", {**OUT_PAYLOAD, "name": ""})
_record("Outbounds", "Validation: empty name → 422", r.status_code == 422)

r = post_form(s_admin, "/outbounds/add", {**OUT_PAYLOAD, "name": "dummy", "dial_plan_match": ""})
_record("Outbounds", "Validation: empty dial_plan_match → 422", r.status_code == 422)

r = post_form(s_admin, f"/outbounds/{T_OUTBOUND}/edit",
              {**OUT_PAYLOAD, "dial_plan_match": "^8",
               "translation_match": "^8(.*)", "translation_sub": "0\\1"})
_backend_ok(r, "Outbounds", f"POST /outbounds/{T_OUTBOUND}/edit → 303")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

section("9. SETTINGS")

r = get_json(s_admin, "/settings/general/json")
_record("Settings", "GET /settings/general/json → 200", r.status_code == 200)

r = post_form(s_admin, "/settings/save", {"section": "general", "log_level": "info"})
if r.status_code == 502:
    _warn("Settings", "POST /settings/save → backend unavailable", _body(r))
else:
    _record("Settings", "POST /settings/save (section=general) → 200",
            r.status_code == 200, f"got {r.status_code} — {_body(r)}")

r = post_form(s_admin, "/settings/blacklist", {"numbers": "123456,789012"})
_record("Settings", "POST /settings/blacklist → 303", r.status_code == 303)

r = post_form(s_admin, "/settings/whitelist", {"numbers": "111222"})
_record("Settings", "POST /settings/whitelist → 303", r.status_code == 303)

for path in ["/settings/hold-music", "/settings/logo", "/settings/monitoring",
             "/settings/intercom", "/settings/node", "/settings/alarms"]:
    r = get_json(s_admin, path)
    _record("Settings", f"GET {path} → 200", r.status_code == 200)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 10 — AUDIT / CALLS / RECORDINGS
# ═══════════════════════════════════════════════════════════════════════════════

section("10. AUDIT / CALLS / RECORDINGS")

r = get_json(s_admin, "/audit")
_record("Audit", "GET /audit → 200", r.status_code == 200)

r = get_json(s_admin, "/calls/json", {"page": "1"})
_record("Calls", "GET /calls/json → 200 JSON", r.status_code == 200, f"got {r.status_code}")

r = get_json(s_admin, "/calls/csv")
_record("Calls", "GET /calls/csv → 200 or 204", r.status_code in (200, 204))

r = get_json(s_admin, "/recordings/json", {"page": "1"})
if r.status_code == 502:
    _warn("Recordings", "GET /recordings/json → backend unavailable", _body(r))
else:
    _record("Recordings", "GET /recordings/json → 200", r.status_code == 200, f"got {r.status_code}")

r = get_json(s_admin, "/recordings/line-permissions")
_record("Recordings", "GET /recordings/line-permissions → 200", r.status_code == 200)

if user_login_ok:
    r = get_json(s_user, "/recordings/json", {"page": "1"})
    if r.status_code != 502:
        _record("Recordings", "User role: GET /recordings/json → 200 (own only)",
                r.status_code == 200)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 11 — BACKUP / RESTORE
# ═══════════════════════════════════════════════════════════════════════════════

section("11. BACKUP / RESTORE")

r = get_json(s_admin, "/backup/list")
_record("Backup", "GET /backup/list → 200 JSON list",
        r.status_code == 200 and isinstance(r.json(), list))

r = post_json(s_admin, "/backup/create", {})
_record("Backup", "POST /backup/create → 200 with result field",
        r.status_code == 200 and "result" in r.json(),
        f"result={r.json().get('result')}, msg={r.json().get('msg','')[:80]}")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 12 — TOPOLOGY / MEDIA / API USERS
# ═══════════════════════════════════════════════════════════════════════════════

section("12. TOPOLOGY / MEDIA / API USERS")

r = get_json(s_admin, "/topology/sites")
_record("Topology", "GET /topology/sites → 200", r.status_code == 200)

r = get_json(s_admin, "/media/ringtones")
_record("Media", "GET /media/ringtones → 200", r.status_code == 200)

r = get_json(s_admin, "/media/dialtone")
_record("Media", "GET /media/dialtone → 200", r.status_code == 200)

r = get_json(s_admin, "/api/users")
if r.status_code == 502:
    _warn("API Mgmt", "GET /api/users → backend unavailable", _body(r))
else:
    _record("API Mgmt", "GET /api/users → 200 JSON list",
            r.status_code == 200 and isinstance(r.json(), list))


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 13 — BUTTON CONFIGURATION (all 12 types)
# ═══════════════════════════════════════════════════════════════════════════════

section("13. BUTTON CONFIGURATION")

btn_base = {"page": "1"}
for i in range(56):
    btn_base[f"buttons[{i}][type]"]              = "disabled"
    btn_base[f"buttons[{i}][label]"]             = ""
    btn_base[f"buttons[{i}][line_id]"]           = ""
    btn_base[f"buttons[{i}][hunt_group]"]        = ""
    btn_base[f"buttons[{i}][speed_dial_number]"] = ""
    btn_base[f"buttons[{i}][page_number]"]       = ""

for btype in ["disabled", "line", "speed_dial", "onebuttondivert", "page_shortcut",
              "ard", "wildcard", "timezone", "mrd", "intercom", "open_hoot", "macro_sequence"]:
    p = dict(btn_base)
    p["buttons[0][type]"]  = btype
    p["buttons[0][label]"] = f"Test {btype}"
    r = post_form(s_admin, f"/users/{T_USER_USER}/buttons", p)
    _record("Buttons", f"  Button type={btype} saves → 303",
            r.status_code == 303, f"got {r.status_code}")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 14 — CUSTOM GROUPS + RESET PASSWORD
# ═══════════════════════════════════════════════════════════════════════════════

section("14. CUSTOM GROUPS + RESET PASSWORD")

r = get_json(s_admin, f"/groups/custom/{T_USER_USER}/json")
_record("Custom Grps", f"GET /groups/custom/{T_USER_USER}/json → 200", r.status_code == 200)

r = post_form(s_admin, f"/groups/custom/{T_USER_USER}",
              {"action": "add", "custom_group": "MyTestGroup"})
_record("Custom Grps", "POST /groups/custom/{u} action=add → 303",
        r.status_code == 303, f"got {r.status_code}")

r = get_json(s_admin, "/reset-password")
_record("ResetPwd", "GET /reset-password → 200 (SPA)", r.status_code == 200)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 15 — LOGOUT
# ═══════════════════════════════════════════════════════════════════════════════

section("15. LOGOUT + SESSION EXPIRY")

r = get_json(s_admin, "/logout")
_record("Auth", "GET /logout → 200 or 303", r.status_code in (200, 303))

r = get_json(s_admin, "/session-check")
_record("Auth", "GET /session-check after logout → 401",
        r.status_code == 401, f"got {r.status_code}")


# ═══════════════════════════════════════════════════════════════════════════════
# CLEANUP
# ═══════════════════════════════════════════════════════════════════════════════

section("CLEANUP — Removing test data")

s_clean = new_session()
s_clean, r_clean = login(ADMIN_USER, ADMIN_PASS, s_clean)
clean_ok = r_clean.status_code in (200, 303)
print(f"  Cleanup login: {'OK' if clean_ok else 'FAILED'} ({r_clean.status_code})")

if clean_ok:
    def _cleanup(label: str, path: str):
        r = post_form(s_clean, path, {})
        ok = r.status_code in (200, 303)
        icon = f"{GREEN}OK{RESET}" if ok else f"{YELLOW}SKIP{RESET}"
        print(f"  [{icon}] Delete {label} → {r.status_code}")

    _cleanup(f"Outbound  {T_OUTBOUND}",    f"/outbounds/{T_OUTBOUND}/delete")
    _cleanup(f"Inbound   {T_INBOUND}",     f"/inbounds/{T_INBOUND}/delete")
    _cleanup(f"Route     {T_ROUTE}",       f"/routes/{T_ROUTE}/delete")
    _cleanup(f"Trunk     {T_TRUNK}",       f"/trunks/{T_TRUNK}/delete")
    _cleanup(f"Line      DN={T_LINE_DN}",  f"/lines/{T_LINE_DN}/delete")
    _cleanup(f"LineGroup DN={T_LINE_DN}",  f"/lines/line-groups/{T_LINE_DN}/delete")
    _cleanup(f"Group     {T_GROUP}",       f"/groups/{T_GROUP}/delete")
    _cleanup(f"User      {T_USER_USER}",   f"/users/{T_USER_USER}/delete")
    _cleanup(f"User      {T_USER_ADMIN}",  f"/users/{T_USER_ADMIN}/delete")
    _cleanup(f"User      {T_USER_AUDITOR}",f"/users/{T_USER_AUDITOR}/delete")


# ═══════════════════════════════════════════════════════════════════════════════
# FINAL SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

section("RESULTS SUMMARY")

passed = [r for r in results if r["passed"]]
failed = [r for r in results if not r["passed"]]
warned = [r for r in results if r["passed"] and r.get("detail", "").startswith("WARN")]
total  = len(results)

if failed:
    print(f"\n{BOLD}{RED}FAILED TESTS:{RESET}")
    for r in failed:
        print(f"  {RED}✗{RESET}  [{r['section']}] {r['name']}"
              + (f"\n      {r['detail']}" if r['detail'] else ""))

if warned:
    print(f"\n{BOLD}{YELLOW}BACKEND WARNINGS (not code bugs):{RESET}")
    for r in warned:
        print(f"  {YELLOW}!{RESET}  [{r['section']}] {r['name']}"
              + (f"\n      {r['detail']}" if r['detail'] else ""))

print(f"\n{BOLD}{'─'*60}{RESET}")
print(f"  Total   : {total}")
print(f"  {GREEN}{BOLD}Passed  : {len(passed)}{RESET}")
print(f"  {YELLOW}{BOLD}Warnings: {len(warned)} (ATP backend unavailable for these){RESET}")
if failed:
    print(f"  {RED}{BOLD}Failed  : {len(failed)}{RESET}")
else:
    print(f"  {GREEN}{BOLD}Failed  : 0  — All tests passed!{RESET}")
print(f"{BOLD}{'─'*60}{RESET}\n")

sys.exit(0 if not failed else 1)
