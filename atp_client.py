"""
atp_client.py — async Unix socket client for the ATP Config Node.

Socket path:  {ATPMGR_DATADIR}/control
Protocol:     newline-delimited JSON  {"type": "...", "payload": {...}}
Timeout:      10 seconds

Wire protocol reference: docs/step-02-changes-cpp.md §6
"""

import asyncio
import json
import os
from typing import Any

from logging_config import logger

# ── Exceptions ────────────────────────────────────────────────────────────────

class AtpBackendError(RuntimeError):
    """Raised when the backend is unreachable or returns an unexpected reply."""


# ── Configuration ─────────────────────────────────────────────────────────────

SOCKET_TIMEOUT = 10  # seconds


def _socket_path() -> str:
    datadir = os.environ.get("ATPMGR_DATADIR", "")
    if not datadir:
        raise AtpBackendError(
            "ATPMGR_DATADIR is not set. "
            "Point it to the directory containing the 'control' Unix socket."
        )
    return os.path.join(datadir, "control")


# ── Low-level transport ───────────────────────────────────────────────────────

async def _send_message(msg_type: str, payload: Any = None) -> dict:
    path = _socket_path()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(path, limit=16 * 1024 * 1024),
            timeout=SOCKET_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise AtpBackendError(f"Timed out connecting to ATP backend at {path!r}")
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        raise AtpBackendError(f"Cannot connect to ATP backend at {path!r}: {exc}") from exc

    msg = json.dumps({"type": msg_type, "payload": payload if payload is not None else {}}) + "\n"
    logger.debug("ATP → %s  payload=%r", msg_type, payload)

    writer.write(msg.encode())
    await writer.drain()

    try:
        raw = await asyncio.wait_for(reader.readline(), timeout=SOCKET_TIMEOUT)
    except asyncio.TimeoutError:
        writer.close()
        raise AtpBackendError("ATP backend did not reply within timeout")

    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass

    if not raw:
        logger.error("ATP closed connection without reply to %s", msg_type)
        raise AtpBackendError("Backend closed the connection without replying.")

    reply = json.loads(raw.decode())
    reply_type = reply.get("type", "")
    logger.debug("ATP ← %s  type=%s", msg_type, reply_type)
    if reply_type in ("node_dml_error", "error"):
        msg = reply.get("payload", {})
        if isinstance(msg, dict):
            msg = msg.get("message", str(msg))
        raise AtpBackendError(f"{msg_type}: {msg}")
    return reply


# ── Payload helpers ───────────────────────────────────────────────────────────

def _extract_list(payload, *extra_keys) -> list:
    """Coerce a payload (list or dict wrapping a list) to a plain list."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if "message" in payload and len(payload) == 1:
            raise AtpBackendError(payload["message"])
        for key in ("users", "lines", "groups", "trunks", "routes", "sites",
                    "recordings", "ringtones", "nodes", "entries", "results",
                    "data", "items", "result", *extra_keys):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
        logger.warning("_extract_list: unexpected payload keys: %s — payload: %r",
                       list(payload.keys()), payload)
    return []


def _extract_dict(payload) -> dict:
    """Coerce a payload to a dict."""
    if isinstance(payload, dict):
        return payload
    return {}


# ── Generic helper ────────────────────────────────────────────────────────────

async def request(msg_type: str, payload: Any = None) -> dict:
    return await _send_message(msg_type, payload)


# ── Auth ──────────────────────────────────────────────────────────────────────

async def authenticate(username: str, password: str) -> dict:
    reply = await _send_message(
        "controller_request_authenticate",
        {"username": username, "password": password},
    )
    logger.debug("authenticate reply: %s", reply)   # ← add this line
    if reply.get("type") == "node_announce_login_succeeded":
        p = reply.get("payload", {})
        return _extract_dict(p)
    msg = "Invalid username or password."
    p = reply.get("payload")
    if isinstance(p, dict):
        msg = p.get("message", msg)
    elif isinstance(p, str):
        msg = p or msg
    raise ValueError(msg)


# ── Users ─────────────────────────────────────────────────────────────────────

def _normalise_user(u: dict) -> dict:
    """Remap C++ field names to the keys the frontend expects."""
    if "user_id" in u and "turret_login" not in u:
        u = {**u, "turret_login": u["user_id"]}
    return u


async def user_search(username: str = "") -> list[dict]:
    reply = await _send_message("controller_user_search", {"username": username})
    return [_normalise_user(u) for u in _extract_list(reply.get("payload", []))]


async def user_get(guid: str) -> dict:
    reply = await _send_message("controller_user_search", {"guid": guid})
    p = reply.get("payload", {})
    if isinstance(p, list) and p:
        return _normalise_user(p[0])
    return _normalise_user(_extract_dict(p))


def _user_login_payload(user: dict) -> dict:
    """Return only the fields UserLogin.cpp deserialises for controller_user_add/update.
    C++ key for turret login is 'user_id', not 'turret_login'.
    All extra fields (group_ids, booleans, settings) are stripped — they either
    crash the C++ JSON parser or are handled via separate settings calls."""
    return {
        "username":   user.get("username", ""),
        "password":   user.get("password", ""),
        "first_name": user.get("first_name", ""),
        "last_name":  user.get("last_name", ""),
        "role":       user.get("role", "User"),
        "user_id":    user.get("turret_login", ""),
        "turret_pin": user.get("turret_pin", ""),
    }


async def user_create(user: dict) -> dict:
    reply = await _send_message("controller_user_add", _user_login_payload(user))
    return _extract_dict(reply.get("payload", {}))


async def user_update(user: dict) -> None:
    await _send_message("controller_user_update", _user_login_payload(user))


async def user_delete(username: str) -> None:
    await _send_message("controller_user_remove", {"username": username})


# Settings keys that are saved as turret parameters (separate from the user record)
_TURRET_SETTING_KEYS = [
    "home_page", "home_page_return_timeout",
    "ringer_volume", "ringer_on_off",
    "handsfree_transmit_volume", "left_transmit_broadcast_vol", "left_receive_broadcast_vol",
    "button_label_font_size", "button_cli_font_size", "local_hunt_group",
    "primary_timezone", "auto_toggle_options",
    "display_sleep_time", "display_inactivity_timeout", "display_wakeup_time",
    "ptt_move_to_hset", "group_all_as_unlatch_all", "right_handset_default",
    "auto_show_call_control", "noise_suppression_level",
    "default_handset_timeout", "display_recordings_on_turret",
    "ext_dial_pad_as_std_tele_pad", "normal_dial_pad_as_std_tele_pad",
    "group_all_device", "group_1_device", "group_2_device", "group_3_device",
    "group_4_device", "group_5_device", "group_6_device",
    "vr_retention_period",
]


async def user_settings_get(username: str) -> dict:
    """Fetch per-user turret settings. Returns a flat name→value dict."""
    reply = await _send_message("controller_settings_get", {
        "source": "node",
        "node": {"O": "", "role": "", "CN": username},
    })
    settings = {}
    for s in reply.get("payload", {}).get("settings", []):
        name = s.get("name", "")
        if name:
            settings[name] = s.get("value", "")
    return settings


async def pages_search(turret_login: str) -> list:
    """Fetch page labels for a user by turret login. Returns [{name, number}]."""
    reply = await _send_message("controller_pages_search", turret_login)
    p = reply.get("payload", {})
    if isinstance(p, dict):
        return p.get("pages", [])
    return []


async def pages_update(turret_login: str, pages: list) -> None:
    """Update page labels for a user. pages = [{name, number}]."""
    await _send_message("controller_pages_update", {
        "user_id": turret_login,
        "pages": [{"guid": "", "name": pg["name"], "number": pg["number"]} for pg in pages],
    })


async def user_settings_update(username: str, role: str, settings: dict) -> None:
    """Persist per-user turret settings — one ATP call per key."""
    for name, value in settings.items():
        await _send_message("controller_settings_update", {
            "source": "node",
            "node": {"O": "", "role": role, "CN": username},
            "name": name,
            "value": str(value),
        })


async def user_validate(user: dict) -> str:
    reply = await _send_message("controller_user_validate", user)
    p = reply.get("payload", {})
    if isinstance(p, dict):
        return p.get("message", "")
    return str(p)


async def user_reset_keys() -> None:
    await _send_message("controller_user_reset_keys", {})


async def reset_user_password(username: str, new_password: str) -> None:
    await _send_message("controller_reset_user_password",
                        {"username": username, "password": new_password})


async def ldap_sync() -> str:
    reply = await _send_message("controller_ldap_sync", {})
    p = reply.get("payload", {})
    if isinstance(p, dict):
        return p.get("message", "Sync complete")
    return str(p)


async def force_relogin(username: str) -> None:
    await _send_message("controller_force_relogin", {"username": username})


# ── Buttons ───────────────────────────────────────────────────────────────────

async def button_search(username: str) -> list[dict]:
    reply = await _send_message("controller_button_search", {"user_id": username})
    p = reply.get("payload", {})
    if isinstance(p, dict):
        return p.get("buttons", [])
    return []


async def button_update(username: str, buttons: list[dict]) -> None:
    """Update all buttons for a user — one ATP call per button."""
    for btn in buttons:
        btn_type = btn.get("type", 0)
        payload = {
            "user_id":           username,
            "guid":              btn.get("guid", 0),  # Required by C++ ButtonConfiguration
            "button_id":         btn.get("position", 0),
            "type":              btn_type,
            "label":             btn.get("label", ""),
            "line_name":         btn.get("line_name", ""),
            "hunt_group_id":     btn.get("hunt_group_id", 0),
            "speed_dial_number": btn.get("speed_dial_number", ""),
            "float":             btn.get("float", 0),
            "ring":              btn.get("ring", 0),
            "privacy":           btn.get("privacy", 0),
            "tx_volume":         0,
            "rx_volume":         0,
            "ringtone":          btn.get("ringtone", ""),
            "forward_condition": btn.get("forward_condition", "always"),
            "button_colour":     btn.get("button_colour", ""),
        }

        if btn.get("guid"):
            payload["action"] = "Update"
            cmd = "controller_button_update"
        else:
            cmd = "controller_button_add"

        logger.info("Sending %s: %r", cmd, payload)
        await _send_message(cmd, payload)


async def button_add_all(username: str, buttons: list[dict]) -> None:
    """Add all buttons for a user in a single controller_button_add call.

    Each button dict must have string 'type' (e.g. "line", "speed_dial") and
    'position' (1-based int).  This maps to C++ ButtonConfigurations which the
    AddOperation deserialises as {"user_id": ..., "buttons": [...]}.
    """
    btn_list = _build_btn_list(buttons)
    payload = {"user_id": username, "buttons": btn_list}
    logger.info("button_add_all: user=%r count=%d", username, len(btn_list))
    await _send_message("controller_button_add", payload)


def _build_btn_list(buttons: list[dict]) -> list[dict]:
    """Convert internal button dicts to the ButtonConfigurations JSON format."""
    result = []
    for btn in buttons:
        result.append({
            "guid":              str(int(btn.get("guid") or 0) or -1),
            "button_id":         btn.get("position", 0),
            "type":              str(btn.get("type", "disabled")),
            "label":             btn.get("label", ""),
            "line_name":         btn.get("line_name") or btn.get("line", ""),
            "hunt_group_id":     btn.get("hunt_group_id") or btn.get("hunt_group", 0),
            "speed_dial_number": btn.get("speed_dial_number") or btn.get("speedial_no", ""),
            "float":             btn.get("float", 0),
            "ring":              btn.get("ring", 0),
            "privacy":           btn.get("privacy", 0),
            "tx_volume":         0,
            "rx_volume":         0,
            "ringtone":          btn.get("ringtone", ""),
            "forward_condition": btn.get("forward_condition", "always"),
            "button_colour":     btn.get("button_colour", ""),
        })
    return result


async def button_update_batch(username: str, buttons: list[dict]) -> None:
    """Update existing buttons (must have guid) in a single controller_button_update call."""
    if not buttons:
        return
    payload = {"user_id": username, "buttons": _build_btn_list(buttons)}
    logger.info("button_update_batch: user=%r count=%d", username, len(buttons))
    await _send_message("controller_button_update", payload)


async def button_remove_guids(username: str, guids: list[str]) -> None:
    """Remove specific buttons by guid list in a single controller_button_remove call.

    ButtonSearchParams.guids is vector<unsigned> so we send integer values.
    """
    if not guids:
        return
    int_guids = [int(g) for g in guids if g and str(g) != "0"]
    if not int_guids:
        return
    logger.info("button_remove_guids: user=%r count=%d", username, len(int_guids))
    await _send_message("controller_button_remove", {"user_id": username, "guids": int_guids})


async def button_add(button: dict) -> None:
    await _send_message("controller_add_button", button)


async def button_delete(button_id: str) -> None:
    await _send_message("controller_button_delete", {"guid": button_id})


async def button_delete_all(username: str) -> None:
    """Delete all buttons for a user via controller_button_remove."""
    await _send_message("controller_button_remove", {"user_id": username})


async def button_delete_positions(username: str, positions: list) -> None:
    """Delete buttons at the given absolute positions for a user.

    Uses controller_button_update to set each matched button to 'disabled'
    rather than controller_button_remove, which deletes ALL user buttons when
    passed a guids list (observed behaviour — likely a C++ API quirk).
    """
    if not positions:
        return
    all_btns = await button_search(username)

    # Log ALL button fields from first two buttons so we can see exactly what
    # C++ returns — field names and sample values.
    if all_btns:
        for i, sample in enumerate(all_btns[:3]):
            logger.info(
                "button_delete_positions: C++ btn[%d] ALL_FIELDS=%r",
                i, dict(sample),
            )

    pos_set = {int(p) for p in positions}

    # Compute absolute position from whichever fields C++ provides.
    # Priority: explicit "position" → page_no*56+button_id → button_id alone.
    BUTTONS_PER_PAGE = 56

    def _abs(btn: dict) -> int:
        if btn.get("position") is not None:
            return int(btn["position"])
        if btn.get("page_no") is not None:
            return int(btn["page_no"]) * BUTTONS_PER_PAGE + int(btn.get("button_id", 0))
        return int(btn.get("button_id", -1))

    all_abs = [_abs(b) for b in all_btns]
    to_reset = [b for b, ap in zip(all_btns, all_abs) if ap in pos_set and b.get("guid")]
    logger.info(
        "button_delete_positions: user=%r pos_range=%d-%d total=%d matched=%d "
        "all_abs_values=%r",
        username, min(pos_set, default=0), max(pos_set, default=0),
        len(all_btns), len(to_reset),
        sorted(set(all_abs)),          # show distinct computed positions — tells us if relative or absolute
    )

    if not to_reset:
        return

    # Update each matched button to 'disabled' via controller_button_update.
    # This is safer than controller_button_remove which deletes ALL user buttons.
    updates = [
        {
            "guid":              btn["guid"],
            "position":          _abs(btn),
            "type":              "disabled",
            "label":             "",
            "line_name":         "",
            "hunt_group_id":     None,
            "speed_dial_number": "",
            "float":             0,
            "ring":              0,
            "privacy":           0,
            "ringtone":          "",
            "forward_condition": "always",
            "button_colour":     "",
        }
        for btn in to_reset
    ]
    await button_update_batch(username, updates)


async def user_add_contact(username: str, label: str, phone: str, company: str = "") -> None:
    """Add an Outlook contact entry for a user (ports UserManager::add_outlook_contact)."""
    await _send_message("controller_user_add_contact", {
        "username": username,
        "label":    label,
        "phone":    phone,
        "company":  company,
    })


async def user_speaker_setting(username: str, value: str) -> None:
    """Save the 24-channel speaker setting for a user (ports save_speaker_settings)."""
    await _send_message("controller_settings_update", {
        "source": "node",
        "node": {"O": "", "role": "User", "CN": username},
        "name":  "spkr_24_channel_val",
        "value": value,
    })


# ── Lines ─────────────────────────────────────────────────────────────────────

async def line_search(dn: str = "") -> list[dict]:
    # LineSearchRequest expects "linename" (not "dn") per LineOperations.cpp.
    # The C++ Json ctor does not default fuzzy_search / only_black_listed when
    # keys are missing (undefined behaviour). Always send explicit values, and
    # paginate like PHP LineSearcher (record_per_page / page_no) for list-all.
    payload: dict = {
        "fuzzy_search": 0,
        "only_black_listed": 0,
        "page_no": 0,
        "count_per_page": 100000,
    }
    if dn:
        payload["linename"] = dn
    reply = await _send_message("controller_line_search", payload)
    return _extract_list(reply.get("payload", []))


async def line_get(dn: str) -> dict:
    reply = await _send_message(
        "controller_line_search",
        {
            "linename": dn,
            "fuzzy_search": 0,
            "only_black_listed": 0,
        },
    )
    p = _extract_list(reply.get("payload", []))
    return p[0] if p else {}


async def line_create(line: dict) -> dict:
    reply = await _send_message("controller_line_add", line)
    return _extract_dict(reply.get("payload", {}))


async def line_update(line: dict) -> None:
    await _send_message("controller_line_update", line)


async def line_delete(dn: str) -> None:
    await _send_message("controller_line_remove", {"linename": dn})


async def line_add_appearance(line_name: str, username: str) -> None:
    """Assign a user appearance on a line (PHP LineManager::add_appearance)."""
    await _send_message("controller_add_line_configuration", [{"line_name": line_name, "user_guid": username}])


async def line_remove_appearance(line_name: str, username: str) -> None:
    """Remove a user appearance from a line (PHP LineManager::remove_appearance)."""
    await _send_message("controller_remove_line_configuration", [{"line_name": line_name, "user_guid": username}])


async def line_usernames_on_line(line_name: str) -> list[str]:
    """
    Usernames that have an appearance on this line.
    Ports: LineManager::find_usernames_on_line → controller_get_dns_on_line.
    """
    reply = await _send_message("controller_get_dns_on_line", line_name)
    if reply.get("type") == "controller_config_api_data_error":
        return []
    p = reply.get("payload")
    logger.debug("line_usernames_on_line %r raw payload: %r", line_name, p)
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except json.JSONDecodeError:
            logger.warning("line_usernames_on_line: bad payload for %r: %r", line_name, p)
            return []
    # Handle dict payload: look for known list keys
    if isinstance(p, dict):
        for key in ("users", "dns", "cn_list", "usernames", "items", "result", "data"):
            if key in p and isinstance(p[key], list):
                p = p[key]
                break
        else:
            logger.warning("line_usernames_on_line: unexpected dict payload for %r: %r", line_name, list(p.keys()))
            return []
    if not isinstance(p, list):
        return []
    out: list[str] = []
    for item in p:
        if isinstance(item, dict):
            # Try common field names for username
            val = item.get("CN") or item.get("username") or item.get("user") or item.get("name")
            if val is not None:
                out.append(str(val))
        elif isinstance(item, str):
            out.append(item)
    return sorted(set(out))


async def line_validate(line: dict) -> str:
    reply = await _send_message("controller_line_validate", line)
    p = reply.get("payload", {})
    return p.get("message", "") if isinstance(p, dict) else str(p)


# ── Line Groups ───────────────────────────────────────────────────────────────

async def line_group_search() -> list[dict]:
    reply = await _send_message("controller_line_group_search", {})
    return _extract_list(reply.get("payload", []))


async def line_group_create(group: dict) -> None:
    await _send_message("controller_line_group_add", group)


async def line_group_update(group: dict) -> None:
    await _send_message("controller_line_group_edit", group)


async def line_group_delete(main_line: str) -> None:
    await _send_message("controller_line_group_delete", {"main_line": main_line})


# ── Blacklist / Whitelist ─────────────────────────────────────────────────────

async def blacklist_search() -> list[str]:
    reply = await _send_message("controller_settings_get", {"section": "blacklist"})
    p = reply.get("payload", {})
    if isinstance(p, dict):
        raw = p.get("numbers", "")
        return [n.strip() for n in raw.split(",") if n.strip()] if isinstance(raw, str) else list(raw)
    return []


async def whitelist_search() -> list[str]:
    reply = await _send_message("controller_settings_get", {"section": "whitelist"})
    p = reply.get("payload", {})
    if isinstance(p, dict):
        raw = p.get("numbers", "")
        return [n.strip() for n in raw.split(",") if n.strip()] if isinstance(raw, str) else list(raw)
    return []


# ── Groups ────────────────────────────────────────────────────────────────────
#
# C++ command names (TraderGroupStore / TraderGroupUserStore):
#   controller_add_trader_grp          payload: {trader_grp_name, trader_grp_type}
#   controller_get_trader_grps         no payload  (excludes global-group)
#   controller_delete_trader_grps      payload: {trader_grp_name, trader_grp_type}
#   controller_add_trader_grp_user     payload: {user_name, trader_grp_name}
#   controller_get_trader_group_users  payload: plain string (group name)
#   controller_get_trd_grp_user_id     payload: {user_name, trader_grp_name}
#   controller_delete_trader_grp_users payload: plain string (row_id)
#   controller_add_trader_grp_dir      payload: {trader_grp_name, contact_name,
#                                                contact_description, contact_number, source}
#   controller_get_trader_group_dirs   payload: plain string (group name)
#   controller_delete_trader_grp_dirs  payload: plain string (row_id)

async def group_search() -> dict:
    """Returns {trader_grp_name: group_data} dict, with users populated."""
    reply = await _send_message("controller_get_trader_grps", {})
    groups = _extract_list(reply.get("payload", []))
    if not isinstance(groups, list):
        return {}

    result = {}
    for i, g in enumerate(groups):
        if not isinstance(g, dict):
            continue
        name = g.get("trader_grp_name", str(i))
        # Fetch users for each group
        try:
            ur = await _send_message("controller_get_trader_group_users", name)
            members = _extract_list(ur.get("payload", []))
            users = []
            for entry in members:
                if isinstance(entry, dict):
                    uname = entry.get("user_name", "")
                    if uname:
                        users.append(uname)
                elif isinstance(entry, str) and entry:
                    users.append(entry)
            g["users"] = users
        except AtpBackendError:
            g["users"] = []
        result[name] = g
    return result


async def group_get(name: str) -> dict:
    # Get group metadata from full list
    all_groups = await group_search()
    group = all_groups.get(name, {"trader_grp_name": name})

    # Fetch users separately via controller_get_trader_group_users
    try:
        cur_reply = await _send_message("controller_get_trader_group_users", name)
        members = _extract_list(cur_reply.get("payload", []))
        users = []
        for entry in members:
            if isinstance(entry, dict):
                uname = entry.get("user_name", "")
                if uname:
                    users.append(uname)
            elif isinstance(entry, str) and entry:
                users.append(entry)
        group["users"] = users
    except AtpBackendError:
        group["users"] = []

    return group


async def group_create(group: dict) -> None:
    grp_name = group.get("name", "")
    await _send_message("controller_add_trader_grp", {
        "trader_grp_name": grp_name,
        "trader_grp_type": group.get("type", "normal"),
    })
    for username in group.get("users", []):
        try:
            await _send_message("controller_add_trader_grp_user", {
                "user_name": username,
                "trader_grp_name": grp_name,
            })
        except AtpBackendError:
            pass  # non-fatal: user may not exist yet


async def group_update(name: str, data: dict) -> None:
    """Replace group membership with the new user list."""
    new_users = data.get("users", [])

    # Fetch current members (payload is a plain string, not a JSON object)
    try:
        cur_reply = await _send_message("controller_get_trader_group_users", name)
        current = _extract_list(cur_reply.get("payload", []))
    except AtpBackendError:
        current = []

    # Remove each current member via row-id lookup
    for entry in current:
        uname = entry.get("user_name", "") if isinstance(entry, dict) else str(entry)
        if not uname:
            continue
        try:
            id_reply = await _send_message("controller_get_trd_grp_user_id", {
                "user_name": uname,
                "trader_grp_name": name,
            })
            row_id = id_reply.get("payload", "")
            if row_id and isinstance(row_id, str) and row_id.strip():
                await _send_message("controller_delete_trader_grp_users", row_id)
        except AtpBackendError:
            pass

    # Add new members
    for username in new_users:
        try:
            await _send_message("controller_add_trader_grp_user", {
                "user_name": username,
                "trader_grp_name": name,
            })
        except AtpBackendError:
            pass


async def group_delete(name: str) -> None:
    await _send_message("controller_delete_trader_grps", {
        "trader_grp_name": name,
        "trader_grp_type": "",
    })


# ── Group Directory ───────────────────────────────────────────────────────────

async def group_dir_search(group_name: str) -> list[dict]:
    # Payload is a plain string (group name), not an object
    reply = await _send_message("controller_get_trader_group_dirs", group_name)
    return _extract_list(reply.get("payload", []), "entries", "directory")


async def group_dir_create(group_name: str, entry: dict) -> None:
    await _send_message("controller_add_trader_grp_dir", {
        "trader_grp_name": group_name,
        "contact_name":        entry.get("name", ""),
        "contact_description": entry.get("description", ""),
        "contact_number":      entry.get("contact_number", ""),
        "source":              "",
    })


async def group_dir_delete(_group_name: str, entry_id: str) -> None:
    # Payload is a plain string (row_id); group_name not needed by this command
    await _send_message("controller_delete_trader_grp_dirs", entry_id)


async def group_dir_update(group_name: str, entry_id: str, entry: dict) -> None:
    """Update a group directory entry by deleting then re-creating it."""
    await _send_message("controller_delete_trader_grp_dirs", entry_id)
    await _send_message("controller_add_trader_grp_dir", {
        "trader_grp_name":     group_name,
        "contact_name":        entry.get("name", ""),
        "contact_description": entry.get("description", ""),
        "contact_number":      entry.get("contact_number", ""),
        "source":              "",
    })


# ── Custom Groups ─────────────────────────────────────────────────────────────

async def custom_group_search(username: str) -> dict:
    # controller_get_cust_groups_per_user takes a plain string (username)
    reply = await _send_message("controller_get_cust_groups_per_user", username)
    return _extract_dict(reply.get("payload", {}))


async def custom_group_action(username: str, action: str, group_name: str, users: list) -> None:
    # Route to the correct per-action command
    if action == "delete":
        # Get row_id then delete
        try:
            id_reply = await _send_message("controller_get_trader_cust_grp_user_id", {
                "certificate_cn": username,
                "custom_group_name": group_name,
            })
            row_id = id_reply.get("payload", "")
            if row_id and isinstance(row_id, str) and row_id.strip():
                await _send_message("controller_delete_trader_cust_grp_users", row_id)
        except AtpBackendError:
            pass
    else:
        # "add" or any other action — add each user to the custom group
        for u in users:
            try:
                await _send_message("controller_add_cust_trader_grp_user", {
                    "certificate_cn":   u,
                    "custom_group_name": group_name,
                })
            except AtpBackendError:
                pass


# ── Trunks ────────────────────────────────────────────────────────────────────

async def trunk_search() -> list[dict]:
    reply = await _send_message("controller_trunks_search", {})
    return _extract_list(reply.get("payload", []))


async def trunk_get(name: str) -> dict:
    reply = await _send_message("controller_trunks_search", {"name": name})
    trunks = _extract_list(reply.get("payload", []))
    return trunks[0] if trunks else {}


async def trunk_create(trunk: dict) -> None:
    await _send_message("controller_trunks_add", trunk)


async def trunk_update(name: str, trunk: dict) -> None:
    await _send_message("controller_trunks_update", {"name": name, **trunk})


async def trunk_delete(name: str) -> None:
    await _send_message("controller_trunks_remove", {"name": name})


# ── SIP Registrations ─────────────────────────────────────────────────────────

async def sipreg_search(trunk_name: str) -> list[dict]:
    reply = await _send_message("controller_sipreg_search", {"trunk": trunk_name})
    return _extract_list(reply.get("payload", []))


async def sipreg_get(trunk_name: str, reg_id: str) -> dict:
    reply = await _send_message("controller_sipreg_search", {"trunk": trunk_name, "id": reg_id})
    regs = _extract_list(reply.get("payload", []))
    return regs[0] if regs else {}


async def sipreg_create(trunk_name: str, reg: dict) -> None:
    await _send_message("controller_sipreg_add", {"trunk": trunk_name, **reg})


async def sipreg_update(trunk_name: str, reg_id: str, reg: dict) -> None:
    await _send_message("controller_sipreg_update", {"trunk": trunk_name, "id": reg_id, **reg})


async def sipreg_delete(trunk_name: str, reg_id: str) -> None:
    await _send_message("controller_sipreg_remove", {"trunk": trunk_name, "id": reg_id})


# ── Gateways ──────────────────────────────────────────────────────────────────

async def gateway_search() -> list[dict]:
    """Return all Gateway actors across all sites.
    PHP equivalent: SiteManager->sites() + actors_in_site() filtered by node_type=='Gateway'.
    Each returned dict has at minimum a 'name' key (the actor CN).
    """
    result: list[dict] = []
    seen: set[str] = set()
    try:
        site_names = await site_search()
    except AtpBackendError:
        return result
    for site in site_names:
        try:
            actors = await site_actors(site)
        except AtpBackendError:
            continue
        for actor in actors:
            if not isinstance(actor, dict):
                continue
            role = actor.get("role", actor.get("Role", ""))
            if str(role).lower() == "gateway":
                cn = actor.get("CN", actor.get("cn", ""))
                if cn and cn not in seen:
                    seen.add(cn)
                    result.append({"name": cn, "cn": cn, "site": site})
    return result


# ── Inbound Rules ─────────────────────────────────────────────────────────────

async def inbound_search() -> list[dict]:
    reply = await _send_message("controller_inbound_search", {})
    return _extract_list(reply.get("payload", []))


async def inbound_get(name: str) -> dict:
    reply = await _send_message("controller_inbound_search", {"guid": name})
    rules = _extract_list(reply.get("payload", []))
    return rules[0] if rules else {}


async def inbound_create(rule: dict) -> None:
    await _send_message("controller_inbound_add", rule)


async def inbound_update(name: str, rule: dict) -> None:
    await _send_message("controller_inbound_update", {"name": name, **rule})


async def inbound_delete(name: str) -> None:
    await _send_message("controller_inbound_remove", {"guid": name})


# ── Outbound Rules ────────────────────────────────────────────────────────────

async def outbound_search() -> list[dict]:
    reply = await _send_message("controller_outbound_search", {})
    return _extract_list(reply.get("payload", []))


async def outbound_get(name: str) -> dict:
    reply = await _send_message("controller_outbound_search", {"guid": name})
    rules = _extract_list(reply.get("payload", []))
    return rules[0] if rules else {}


async def outbound_create(rule: dict) -> None:
    await _send_message("controller_outbound_add", rule)


async def outbound_update(name: str, rule: dict) -> None:
    await _send_message("controller_outbound_update", {"name": name, **rule})


async def outbound_delete(name: str) -> None:
    await _send_message("controller_outbound_remove", {"guid": name})


# ── Routes ────────────────────────────────────────────────────────────────────

async def route_search() -> list[dict]:
    reply = await _send_message("controller_route_search", {})
    return _extract_list(reply.get("payload", []))


async def route_create(route: dict) -> None:
    await _send_message("controller_route_add", route)


async def route_update(name: str, route: dict) -> None:
    # route dict already contains "route" (name) and "trunks" — send as-is
    await _send_message("controller_route_update", route)


async def route_delete(name: str) -> None:
    await _send_message("controller_route_remove", {"name": name})


# ── Settings ──────────────────────────────────────────────────────────────────

async def settings_get(section: str = "general", node_dn: str = None) -> dict:
    payload: dict = {"source": "global"}  # C++ expects source parameter
    if node_dn:
        payload["source"] = "node"
        payload["node"] = node_dn
    reply = await _send_message("controller_settings_get", payload)
    raw = reply.get("payload", {})
    # C++ returns {"settings": [{name, value, type, source, description}, ...]}
    # Transform to {name: {value, source, description}} for callers
    if isinstance(raw, dict) and "settings" in raw and isinstance(raw["settings"], list):
        return {
            s["name"]: {
                "value":       s.get("value", ""),
                "source":      s.get("source", ""),
                "description": s.get("description", ""),
            }
            for s in raw["settings"]
            if isinstance(s, dict) and "name" in s
        }
    return _extract_dict(raw)


async def settings_update(section: str, data: dict) -> None:
    """Update global settings — one ATP call per key."""
    for name, value in data.items():
        await _send_message("controller_settings_update", {
            "source": "global",
            "name": name,
            "value": str(value),
        })


async def settings_reset(name: str, node_dn: str = None) -> None:
    payload: dict = {"source": "global", "name": name}
    await _send_message("controller_settings_reset", payload)


# ── Hold Music ────────────────────────────────────────────────────────────────

async def hold_music_update(order: list, enabled: str) -> None:
    await _send_message("controller_hold_music_update", {"order": order, "enabled": enabled})


async def hold_music_delete(filename: str) -> None:
    await _send_message("controller_hold_music_delete", {"filename": filename})


# ── Topology / Sites ──────────────────────────────────────────────────────────

async def site_search() -> list[str]:
    """Returns list of site name strings via controller_site_list_sites."""
    reply = await _send_message("controller_site_list_sites", {})
    payload = reply.get("payload", [])
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("sites", "results"):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
    return []


async def site_create(site_name: str) -> None:
    await _send_message("controller_site_create", site_name)


async def site_delete(site: str) -> None:
    await _send_message("controller_site_delete", site)


async def site_move_actors(from_site: str, target: str, actors: list[dict]) -> None:
    """Move actors to target site.
    Payload: MoveActorsToSiteRequest {target: str, actors: [{CN, role, O}]}
    actors should be full actor dicts with CN, role, O keys.
    """
    await _send_message("controller_site_move_actors_to", {
        "target": target,
        "actors": [
            {
                "CN":   a.get("CN",   a.get("cn",   "")),
                "role": a.get("role", a.get("Role", "")),
                "O":    a.get("O",    a.get("o",    "")),
            }
            for a in actors
        ],
    })


async def site_actors(site: str) -> list[dict]:
    """Returns ActorsInSite payload: {site, actors: [{CN, role, O}]}"""
    reply = await _send_message("controller_site_list_actors", site)
    payload = reply.get("payload", {})
    if isinstance(payload, dict):
        actors = payload.get("actors", [])
        return actors if isinstance(actors, list) else []
    if isinstance(payload, list):
        return payload
    return []


# ── Node operations ───────────────────────────────────────────────────────────

async def node_connections() -> list:
    """Return connected nodes as ConnectionInfo dicts.
    Each dict: {local_cn, local_endpoint, remote_endpoint: {host, port},
                creation_date, remote_dn?: {CN, role, O}, remote_contact?}
    Uses controller_request_connections (correct message type).
    """
    reply = await _send_message("controller_request_connections", {})
    return _extract_list(reply.get("payload", []), "connections")


async def node_restart(target: str) -> None:
    await _send_message("controller_notify_to_terminate", target)


async def node_deleted_today() -> list[dict]:
    reply = await _send_message("controller_node_deleted_today", {})
    return _extract_list(reply.get("payload", []))


async def node_search(site: str = "") -> list[dict]:
    reply = await _send_message("controller_node_search", {"site": site})
    return _extract_list(reply.get("payload", []), "nodes")


# ── Audit ─────────────────────────────────────────────────────────────────────

async def audit_search(filters: dict = None) -> dict:
    """Returns {data: list[dict], has_newer: bool, has_older: bool}."""
    reply = await _send_message("controller_audit_search", filters or {})
    payload = reply.get("payload", {})
    if isinstance(payload, dict):
        return {
            "data":      payload.get("data", []) if isinstance(payload.get("data"), list) else [],
            "has_newer": bool(payload.get("has_newer", False)),
            "has_older": bool(payload.get("has_older", False)),
        }
    return {"data": [], "has_newer": False, "has_older": False}


# ── Call logs ─────────────────────────────────────────────────────────────────

async def call_log_search(filters: dict = None) -> list[dict]:
    reply = await _send_message("controller_call_log_search", filters or {})
    return _extract_list(reply.get("payload", []))


async def monitoring_log_add(date: str, node_cn: str, node_type: str, host: str, operation: str) -> None:
    await _send_message("controller_add_monitoring_log", {
        "date":      date,
        "node_cn":   node_cn,
        "node_type": node_type,
        "host":      host,
        "operation": operation,
    })


# ── Ringtones / Media ─────────────────────────────────────────────────────────

async def ringtone_search() -> list[dict]:
    reply = await _send_message("controller_ringtone_search", {})
    return _extract_list(reply.get("payload", []))


async def ringtone_create(ringtone: dict) -> None:
    await _send_message("controller_ringtone_add", ringtone)


async def ringtone_update(ringtone: dict) -> None:
    await _send_message("controller_ringtone_update", ringtone)


async def ringtone_delete(name: str) -> None:
    await _send_message("controller_ringtone_remove", {"name": name})


async def ringtone_set_default(name: str) -> None:
    await _send_message("controller_ringtone_set_default", {"name": name})


# ── Voice Recording Servers (VRS) ─────────────────────────────────────────────

async def vrs_list() -> list[dict]:
    """
    List all Voice Recording Servers across all sites.

    controller_site_list_vrs expects an actor CN (not a site name) and
    returns VRS servers visible to that actor's site.  We pick one actor
    per site and combine the unique results.
    """
    # Get site names, then fetch actors per site
    try:
        raw_sites = await site_search()
    except AtpBackendError:
        raw_sites = []

    site_names: list[str] = []
    for s in raw_sites:
        if isinstance(s, str):
            site_names.append(s)
        elif isinstance(s, dict):
            name = s.get("name") or s.get("id") or ""
            if name:
                site_names.append(name)
    if not site_names:
        site_names = ["default"]

    # Fetch actors for each site using controller_site_list_actors
    site_actors_map: dict[str, list[str]] = {}
    for site_name in site_names:
        try:
            actors = await site_actors(site_name)
            cns = []
            for a in actors:
                if isinstance(a, str):
                    cns.append(a)
                elif isinstance(a, dict):
                    cn = a.get("CN") or a.get("name") or a.get("cn") or ""
                    if cn:
                        cns.append(cn)
            site_actors_map[site_name] = cns
        except AtpBackendError:
            site_actors_map[site_name] = []


    all_vrs: list[dict] = []
    seen: set[tuple] = set()

    for site_name, actors in site_actors_map.items():
        # Pick best candidate: prefer gateway/vrs actor, fall back to first actor
        candidate = next(
            (a for a in actors if a and any(kw in a.lower() for kw in ("gateway", "vrs", "config"))),
            next((a for a in actors if a), None),
        )
        if not candidate:
            continue
        try:
            reply = await _send_message("controller_site_list_vrs", candidate)
            for item in _extract_list(reply.get("payload", [])):
                if isinstance(item, dict) and item.get("ip"):
                    key = (item.get("name", ""), item.get("ip", ""))
                    if key not in seen:
                        seen.add(key)
                        all_vrs.append(item)
        except AtpBackendError:
            continue

    return all_vrs


async def vrs_create(name: str, ip: str, site: str) -> None:
    """Create a VRS entry (PHP SiteManager::create_vr)."""
    await _send_message("controller_site_create_vr", {"name": name, "ip": ip, "site": site})


async def vrs_delete(name: str) -> None:
    """Delete a VRS entry by name (PHP SiteManager::delete_vr)."""
    await _send_message("controller_site_delete_vr", name)


# ── Recordings ────────────────────────────────────────────────────────────────

async def recording_search(filters: dict, page: int = 1, per_page: int = 25) -> dict:
    payload = {**filters, "page": page, "per_page": per_page}
    reply = await _send_message("controller_recording_search", payload)
    p = reply.get("payload", {})
    if isinstance(p, dict):
        return p
    return {"results": _extract_list(p), "total": 0}


async def recording_play(rec_id: str) -> dict:
    reply = await _send_message("controller_recording_play", {"id": rec_id})
    return _extract_dict(reply.get("payload", {}))


async def recording_download(rec_id: str) -> dict:
    reply = await _send_message("controller_recording_download", {"id": rec_id})
    return _extract_dict(reply.get("payload", {}))


async def recording_line_permissions_get(username: str) -> list:
    reply = await _send_message("controller_recording_permissions_get", {"username": username})
    p = reply.get("payload", [])
    return _extract_list(p, "lines", "permissions")


async def recording_line_permissions_set(username: str, allowed: list) -> None:
    await _send_message("controller_recording_permissions_set",
                        {"username": username, "lines": allowed})


# ── Backup / Restore ──────────────────────────────────────────────────────────

async def backup_create(backup_path: str) -> str:
    """C++ BackupManager::backup_amp sends path as plain string."""
    reply = await _send_message("controller_amp_backup_request", backup_path)
    p = reply.get("payload", "")
    return str(p) if p else "Backup requested"


async def restore_apply(restore_path: str) -> str:
    """C++ RestoreManager::restore_amp sends path as plain string."""
    reply = await _send_message("controller_amp_restore_request", restore_path)
    p = reply.get("payload", "")
    return str(p) if p else "Restore requested"


# ── REST / API users ──────────────────────────────────────────────────────────

async def api_user_search() -> list[dict]:
    reply = await _send_message("controller_rest_user_search", {})
    return _extract_list(reply.get("payload", []))


async def api_user_create(data: dict) -> dict:
    """Create a REST API user; returns {client_id, client_secret, ...}."""
    reply = await _send_message("controller_rest_user_add", data)
    return _extract_dict(reply.get("payload", {}))


async def api_user_update(username: str, data: dict) -> None:
    await _send_message("controller_rest_user_update", {"username": username, **data})


async def api_user_delete(username: str) -> None:
    await _send_message("controller_rest_user_remove", {"username": username})


async def api_user_credentials_get(username: str) -> dict:
    reply = await _send_message("controller_rest_user_credentials", {"username": username})
    return _extract_dict(reply.get("payload", {}))


async def api_user_regenerate_credentials(username: str) -> dict:
    """Regenerate credentials; returns {client_id, client_secret}."""
    reply = await _send_message("controller_rest_user_regenerate", {"username": username})
    return _extract_dict(reply.get("payload", {}))


# ── OAuth / Token operations ───────────────────────────────────────────────────
#
# C++ command names (RestTokenManager):
#   controller_rest_token_get       payload: {client_id, client_secret}
#   controller_rest_token_refresh   payload: {client_id, client_secret, refresh_token}
#   controller_rest_token_verify    payload: {access_token, api_name}

async def rest_token_get(client_id: str, client_secret: str) -> dict:
    """Exchange client credentials for an access token + refresh token."""
    reply = await _send_message("controller_rest_token_get", {
        "client_id": client_id,
        "client_secret": client_secret,
    })
    return _extract_dict(reply.get("payload", {}))


async def rest_token_refresh(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """Exchange a refresh token for a new access token."""
    reply = await _send_message("controller_rest_token_refresh", {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    })
    return _extract_dict(reply.get("payload", {}))


async def rest_token_verify(access_token: str, api_name: str = "") -> dict:
    """Verify an access token and optionally check API-level access rights."""
    reply = await _send_message("controller_rest_token_verify", {
        "access_token": access_token,
        "api_name": api_name,
    })
    return _extract_dict(reply.get("payload", {}))


async def api_user_reset_password(username: str, new_password: str) -> None:
    """Update an API user's password."""
    await _send_message("controller_rest_user_update", {
        "username": username,
        "password": new_password,
    })


# ── Health ────────────────────────────────────────────────────────────────────

async def echo() -> bool:
    try:
        reply = await _send_message("controller_echo", {})
        return bool(reply)
    except AtpBackendError:
        return False
