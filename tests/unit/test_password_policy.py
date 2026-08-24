"""tests/unit/test_password_policy.py — composition rules and first-login store."""

from pathlib import Path

import password_policy as pp


def test_password_too_short():
    assert pp.password_error("Ab1!") == pp.PASSWORD_RULES_MESSAGE


def test_password_missing_digit():
    assert pp.password_error("Abcdefg!") == pp.PASSWORD_RULES_MESSAGE


def test_password_missing_letter():
    assert pp.password_error("12345678!") == pp.PASSWORD_RULES_MESSAGE


def test_password_missing_special():
    assert pp.password_error("Abcdefg1") == pp.PASSWORD_RULES_MESSAGE


def test_password_valid_complex():
    assert pp.password_error("Good#pass1") is None


def test_missing_flag_is_false(tmp_path, monkeypatch):
    monkeypatch.setenv("MUST_CHANGE_PASSWORD_FILE", str(tmp_path / "flags.json"))
    pp.reset_store()
    assert pp.get_must_change("alice") is False
    assert pp.get_must_change("") is False


def test_mark_and_clear_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("MUST_CHANGE_PASSWORD_FILE", str(tmp_path / "flags.json"))
    pp.reset_store()
    pp.mark_must_change("alice")
    assert pp.get_must_change("alice") is True
    assert pp.get_must_change("bob") is False
    pp.clear_must_change("alice")
    assert pp.get_must_change("alice") is False
    data = Path(tmp_path / "flags.json").read_text()
    assert "alice" not in data or '"alice"' not in data or "true" not in data.lower()
