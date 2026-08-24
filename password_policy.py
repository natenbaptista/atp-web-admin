"""
password_policy.py — AMP webadmin user-password rules and first-login flag.

ATP controller_user_add/update only accepts a fixed C++ field set, so the
must_change_password flag is kept in a sidecar JSON file (or in memory when
no datadir is configured). Missing username / missing file = False, so
existing users are not forced to change their password.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional

PASSWORD_MIN_LENGTH = 8

PASSWORD_RULES_MESSAGE = (
    "Password must be at least 8 characters and include a letter, "
    "a number, and a special character."
)

PASSWORD_SAME_AS_CURRENT_MESSAGE = (
    "New password must be different from the current password."
)

PASSWORD_RULES = [
    "At least 8 characters",
    "At least one letter, one number, and one special character",
    "New users, and users whose password is reset by an admin, must change this password at next login",
]


_lock = threading.Lock()
_memory: dict[str, bool] = {}


def password_error(password: str) -> Optional[str]:
    """Return a user-facing error if password is weak, else None.

    Access PIN / turret_pin must not be passed here.
    """
    if password is None:
        return PASSWORD_RULES_MESSAGE
    if len(password) < PASSWORD_MIN_LENGTH:
        return PASSWORD_RULES_MESSAGE
    has_letter = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(not c.isalnum() for c in password)
    if not (has_letter and has_digit and has_special):
        return PASSWORD_RULES_MESSAGE
    return None


def _file_path() -> Optional[Path]:
    override = os.environ.get("MUST_CHANGE_PASSWORD_FILE", "").strip()
    if override:
        return Path(override)
    datadir = os.environ.get("ATPMGR_DATADIR", "").strip()
    if datadir:
        return Path(datadir) / "must_change_password.json"
    return None


def reset_store() -> None:
    """Clear the in-memory map. Used by tests; does not delete a datadir file."""
    global _memory
    with _lock:
        _memory = {}


def _load_unlocked() -> dict[str, bool]:
    path = _file_path()
    if path is None:
        return dict(_memory)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, bool] = {}
    for key, val in data.items():
        name = str(key or "").strip()
        if not name:
            continue
        if val is True or (isinstance(val, str) and val.lower() in ("1", "true", "yes")):
            out[name] = True
    return out


def _save_unlocked(data: dict[str, bool]) -> None:
    global _memory
    path = _file_path()
    if path is None:
        _memory = dict(data)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def get_must_change(username: str) -> bool:
    """True only when the username is explicitly flagged. Missing = False."""
    name = (username or "").strip()
    if not name:
        return False
    with _lock:
        return bool(_load_unlocked().get(name, False))


def mark_must_change(username: str) -> None:
    name = (username or "").strip()
    if not name:
        return
    with _lock:
        data = _load_unlocked()
        data[name] = True
        _save_unlocked(data)


def clear_must_change(username: str) -> None:
    name = (username or "").strip()
    if not name:
        return
    with _lock:
        data = _load_unlocked()
        if name in data:
            del data[name]
            _save_unlocked(data)
