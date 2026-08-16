"""
routers/directory.py — /directory/*

1. Global LDAP/system directory search (users + lines via ATP)
   GET  /directory          → search page (SPA)
   GET  /directory/search   → AJAX search (JSON, up to 200 results)

2. Global Directory management (portable SQLite + txt file)
   GET  /directory/global                      → list all entries (JSON)
   POST /directory/global/add                  → add entry (multipart)
   GET  /directory/global/{id}                 → get single entry (JSON)
   POST /directory/global/{id}/edit            → update entry (multipart)
   POST /directory/global/{id}/delete          → delete entry
   POST /directory/global/delete-multiple      → bulk delete
   POST /directory/global/upload-profile-images → bulk upload profile pics
   POST /directory/global/upload-company-logos  → bulk upload logos
   GET  /directory/global/image/{type}/{filename} → serve image
   POST /directory/global/import               → import CSV or colon txt
   GET  /directory/global/export               → export CSV
   GET  /directory/global/export-profile-images → export ZIP
   GET  /directory/global/export-company-logos  → export ZIP
   POST /directory/global/update-global        → regenerate txt file

Storage: SQLite at {GLOBAL_DIR_PATH}/global_directory.db
         (default GLOBAL_DIR_PATH=/home/atp/global_directory)
The txt file is written ONLY by POST /directory/global/update-global.
"""

import asyncio
import csv
import io
import os
import pathlib
import re
import sqlite3
import zipfile
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

import atp_client
from session import require_session
from logging_config import logger

router = APIRouter(prefix="/directory")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"

_MAX_RESULTS = 200
_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "bmp", "webp"}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB
_ID_RE = re.compile(r"^\d{7}$")

_CSV_FIELDS = [
    "cn", "name", "phone1", "profile_pic", "company_name", "company_logo",
    "company_type", "group", "email", "company_address", "phone2", "phone3",
    "designation",
]

_HEADER_ALIASES = {
    "gd_unique_id": "id",
    "gd_type": "cn",
    "gd_name": "name",
    "gd_number": "phone1",
    "gd_profile_pic": "profile_pic",
    "gd_company": "company_name",
    "gd_logo": "company_logo",
    "gd_company_type": "company_type",
    "gd_group": "group",
    "gd_email": "email",
    "gd_address": "company_address",
    "gd_number_2": "phone2",
    "gd_number_3": "phone3",
    "gd_designation": "designation",
}


# ── Paths ──────────────────────────────────────────────────────────────────────

def _global_dir_base() -> pathlib.Path:
    """Root of the portable global directory on disk."""
    base = os.environ.get("GLOBAL_DIR_PATH", "/home/atp/global_directory")
    return pathlib.Path(base)


def _profile_dir() -> pathlib.Path:
    return _global_dir_base() / "profile_picture"


def _logo_dir() -> pathlib.Path:
    """If {base}/logo exists use it, elif {base}/logos exists use it, else create logos/."""
    base = _global_dir_base()
    logo = base / "logo"
    logos = base / "logos"
    if logo.is_dir():
        return logo
    if logos.is_dir():
        return logos
    logos.mkdir(parents=True, exist_ok=True)
    return logos


def _txt_file() -> pathlib.Path:
    return _global_dir_base() / "global_directory.txt"


def _db_path() -> pathlib.Path:
    return _global_dir_base() / "global_directory.db"


# ── SQLite helpers ─────────────────────────────────────────────────────────────

_TABLE_READY = False
_INIT_LOCK = asyncio.Lock()


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _row_to_dict(r) -> dict:
    return {
        "id":              r["id"],
        "cn":              r["cn"] or "",
        "name":            r["name"] or "",
        "phone1":          r["phone1"] or "",
        "profile_pic":     r["profile_pic"] or "",
        "company_name":    r["company_name"] or "",
        "company_logo":    r["company_logo"] or "",
        "company_type":    r["company_type"] or "",
        "group":           r["grp"] or "",
        "email":           r["email"] or "",
        "company_address": r["company_address"] or "",
        "phone2":          r["phone2"] or "",
        "phone3":          r["phone3"] or "",
        "designation":     r["designation"] or "",
    }


def _ensure_dirs() -> None:
    _global_dir_base().mkdir(parents=True, exist_ok=True)
    _profile_dir().mkdir(parents=True, exist_ok=True)
    _logo_dir()  # uses existing logo/ or logos/, else creates logos/


def _create_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS global_directory (
            id               INTEGER PRIMARY KEY,
            cn               TEXT,
            name             TEXT    NOT NULL,
            phone1           TEXT,
            profile_pic      TEXT,
            company_name     TEXT,
            company_logo     TEXT,
            company_type     TEXT,
            grp              TEXT,
            email            TEXT,
            company_address  TEXT,
            phone2           TEXT,
            phone3           TEXT,
            designation      TEXT
        )
        """
    )


def _insert_row(conn: sqlite3.Connection, d: dict, explicit_id: Optional[int] = None) -> dict:
    fields = (
        d.get("cn", "") or "",
        d.get("name", "") or "",
        d.get("phone1", "") or "",
        d.get("profile_pic", "") or "",
        d.get("company_name", "") or "",
        d.get("company_logo", "") or "",
        d.get("company_type", "") or "",
        d.get("group", "") or "",
        d.get("email", "") or "",
        d.get("company_address", "") or "",
        d.get("phone2", "") or "",
        d.get("phone3", "") or "",
        d.get("designation", "") or "",
    )
    if explicit_id is not None:
        conn.execute(
            """
            INSERT INTO global_directory
                (id, cn, name, phone1, profile_pic, company_name, company_logo,
                 company_type, grp, email, company_address, phone2, phone3, designation)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            "",
            (int(explicit_id),) + fields,
        )
        rid = int(explicit_id)
    else:
        cur = conn.execute(
            """
            INSERT INTO global_directory
                (cn, name, phone1, profile_pic, company_name, company_logo,
                 company_type, grp, email, company_address, phone2, phone3, designation)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            "",
            fields,
        )
        rid = cur.lastrowid
    row = conn.execute("SELECT * FROM global_directory WHERE id = ?", (rid,)).fetchone()
    return _row_to_dict(row)
