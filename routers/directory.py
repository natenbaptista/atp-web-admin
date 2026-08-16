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
            """,
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
            """,
            fields,
        )
        rid = cur.lastrowid
    row = conn.execute("SELECT * FROM global_directory WHERE id = ?", (rid,)).fetchone()
    return _row_to_dict(row)


def _seed_from_txt(conn: sqlite3.Connection) -> int:
    txt = _txt_file()
    if not txt.exists():
        return 0
    inserted = 0
    for line in txt.read_text(encoding="utf-8", errors="replace").splitlines():
        d = _parse_txt_line(line)
        if not d:
            continue
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO global_directory
                    (id, cn, name, phone1, profile_pic, company_name, company_logo,
                     company_type, grp, email, company_address, phone2, phone3, designation)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    d["id"], d["cn"], d["name"], d["phone1"], d["profile_pic"],
                    d["company_name"], d["company_logo"], d["company_type"],
                    d["group"], d["email"], d["company_address"],
                    d["phone2"], d["phone3"], d["designation"],
                ),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
        except sqlite3.Error as exc:
            logger.warning("Seed skip id=%s: %s", d.get("id"), exc)
    return inserted


def _ensure_table_sync() -> None:
    _ensure_dirs()
    conn = _connect()
    try:
        _create_table(conn)
        count = conn.execute("SELECT COUNT(*) FROM global_directory").fetchone()[0]
        if count == 0:
            n = _seed_from_txt(conn)
            logger.info("Global Directory seeded %s rows from txt", n)
        conn.commit()
    finally:
        conn.close()


async def _ensure_table() -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    async with _INIT_LOCK:
        if _TABLE_READY:
            return
        await asyncio.to_thread(_ensure_table_sync)
        _TABLE_READY = True


def _run_db(fn, *args, **kwargs):
    conn = _connect()
    try:
        result = fn(conn, *args, **kwargs)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def _db(fn, *args, **kwargs):
    await _ensure_table()
    return await asyncio.to_thread(_run_db, fn, *args, **kwargs)


# ── txt file helpers ───────────────────────────────────────────────────────────

def _clean_address(addr: str) -> str:
    return re.sub(r"[\r\n\t]+", " ", (addr or "").strip())


def _format_txt_line(d: dict) -> str:
    """Format one record as a colon-delimited line matching the existing format."""
    return "{:07d}:{}:{}:{}:{}:{}:{}:{}:{}:{}:{}:{}:{}:{}:\n".format(
        int(d["id"]),
        d.get("cn", ""),
        d.get("name", ""),
        d.get("phone1", ""),
        d.get("profile_pic", ""),
        d.get("company_name", ""),
        d.get("company_logo", ""),
        d.get("company_type", ""),
        d.get("group", ""),
        d.get("email", ""),
        _clean_address(d.get("company_address", "")),
        d.get("phone2", ""),
        d.get("phone3", ""),
        d.get("designation", ""),
    )


def _parse_txt_line(line: str) -> Optional[dict]:
    raw = line.rstrip("\r\n")
    if not raw or raw.startswith("GD_"):
        return None
    parts = raw.split(":")
    if parts and parts[-1] == "":
        parts = parts[:-1]
    if not parts or not _ID_RE.match(parts[0]):
        return None
    if len(parts) > 14:
        extra = len(parts) - 14
        parts = parts[:10] + [":".join(parts[10:11 + extra])] + parts[11 + extra:]
    while len(parts) < 14:
        parts.append("")
    return {
        "id":              int(parts[0]),
        "cn":              parts[1],
        "name":            parts[2],
        "phone1":          parts[3],
        "profile_pic":     parts[4],
        "company_name":    parts[5],
        "company_logo":    parts[6],
        "company_type":    parts[7],
        "group":           parts[8],
        "email":           parts[9],
        "company_address": parts[10],
        "phone2":          parts[11],
        "phone3":          parts[12],
        "designation":     parts[13],
    }


def _regenerate_txt(conn: sqlite3.Connection) -> int:
    """Rewrite global_directory.txt from all current DB records."""
    rows = conn.execute("SELECT * FROM global_directory ORDER BY id ASC").fetchall()
    txt = _txt_file()
    txt.parent.mkdir(parents=True, exist_ok=True)
    txt.write_text(
        "".join(_format_txt_line(_row_to_dict(r)) for r in rows),
        encoding="utf-8",
    )
    return len(rows)


# ── Image helpers ─────────────────────────────────────────────────────────────

def _sanitise_filename(name: str) -> str:
    return re.sub(r"\s+", "_", pathlib.Path(name).name)


def _unique_dest(dest_dir: pathlib.Path, filename: str):
    """Never overwrite: stem.ext, then stem_1.ext, stem_2.ext, ..."""
    dest = dest_dir / filename
    if not dest.exists():
        return dest, filename
    stem = dest.stem
    suffix = dest.suffix
    n = 1
    while True:
        candidate = f"{stem}_{n}{suffix}"
        dest = dest_dir / candidate
        if not dest.exists():
            return dest, candidate
        n += 1


async def _save_image(upload: UploadFile, dest_dir: pathlib.Path) -> str:
    filename = _sanitise_filename(upload.filename or "upload")
    ext = pathlib.Path(filename).suffix.lower().lstrip(".")
    if ext not in _IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported image type: {ext}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest, filename = _unique_dest(dest_dir, filename)
    data = await upload.read()
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Image exceeds 10 MB limit")
    dest.write_bytes(data)
    return filename


def _maybe_delete_image(conn: sqlite3.Connection, filename: str, img_dir: pathlib.Path, col: str) -> None:
    if not filename:
        return
    still = conn.execute(
        f"SELECT COUNT(*) FROM global_directory WHERE {col} = ?", (filename,)
    ).fetchone()[0]
    if still == 0:
        try:
            (img_dir / filename).unlink(missing_ok=True)
        except OSError:
            pass


def _validate_name_phone(name: str, phone1: str) -> None:
    if not (name or "").strip():
        raise HTTPException(status_code=400, detail="Name is required")
    if not (phone1 or "").strip():
        raise HTTPException(status_code=400, detail="Phone is required")


def _form_text(form, key: str, default: str = "") -> str:
    """Read a text field from multipart. Ignore UploadFile values (SPA also
    appends profile_pic/company_logo as filename strings)."""
    for v in form.getlist(key):
        if hasattr(v, "filename"):
            continue
        return str(v) if v is not None else default
    return default


def _form_upload(form, key: str):
    """Return the first real file upload for key, if any.

    The React SPA always FormData.append()s profile_pic/company_logo as the
    existing filename string, then optionally appends a File. FastAPI File()
    rejects the string with 422 (Expected UploadFile, received str).
    """
    for v in form.getlist(key):
        if hasattr(v, "filename") and getattr(v, "filename", None):
            return v
    return None


def _entry_payload_from_form(form) -> dict:
    return {
        "cn": _form_text(form, "cn"),
        "name": _form_text(form, "name").strip(),
        "phone1": _form_text(form, "phone1").strip(),
        "company_name": _form_text(form, "company_name"),
        "company_type": _form_text(form, "company_type"),
        "group": _form_text(form, "group"),
        "email": _form_text(form, "email"),
        "company_address": _form_text(form, "company_address"),
        "phone2": _form_text(form, "phone2"),
        "phone3": _form_text(form, "phone3"),
        "designation": _form_text(form, "designation"),
    }


# ── Index (SPA) ──────────────────────────────────────────────────────────────

@router.get("")
async def directory_index():
    return FileResponse(PUBLIC_DIR / "index.html")


# ── AJAX search (users + lines) ────────────────────────────────────────────────

@router.get("/search", response_class=JSONResponse)
async def directory_search(
    q: str = "",
    session: dict = Depends(require_session),
):
    """
    Search global directory (users + lines) by name, username, or DN.
    Returns up to 200 results as JSON.
    """
    q = q.strip()
    if not q or len(q) < 2:
        return []

    results = []

    # Search users
    try:
        users = await atp_client.user_search(q)
        for u in users[:_MAX_RESULTS]:
            results.append({
                "type":        "user",
                "id":          u.get("username", ""),
                "name":        f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()
                               or u.get("username", ""),
                "detail":      u.get("username", ""),
                "role":        u.get("role", ""),
                "url":         f"/users/{u.get('username', '')}/edit",
            })
    except atp_client.AtpBackendError:
        pass

    # Search lines
    try:
        lines = await atp_client.line_search(q)
        for ln in lines[: _MAX_RESULTS - len(results)]:
            results.append({
                "type":   "line",
                "id":     ln.get("dn", ""),
                "name":   ln.get("name", "") or ln.get("dn", ""),
                "detail": ln.get("dn", ""),
                "role":   ln.get("type", ""),
                "url":    f"/lines/{ln.get('dn', '')}/edit",
            })
    except atp_client.AtpBackendError:
        pass

    return results[:_MAX_RESULTS]


# ══════════════════════════════════════════════════════════════════════════════
# Global Directory management (contact book — SQLite)
# ══════════════════════════════════════════════════════════════════════════════

def _list_sync(conn: sqlite3.Connection, q: str) -> list:
    q = (q or "").strip()
    if len(q) < 3:
        rows = conn.execute(
            "SELECT * FROM global_directory ORDER BY name COLLATE NOCASE ASC"
        ).fetchall()
    else:
        term = f"%{q}%"
        rows = conn.execute(
            """
            SELECT * FROM global_directory
            WHERE name LIKE ? COLLATE NOCASE
               OR company_name LIKE ? COLLATE NOCASE
               OR email LIKE ? COLLATE NOCASE
               OR cn LIKE ? COLLATE NOCASE
               OR phone1 LIKE ? COLLATE NOCASE
               OR phone2 LIKE ? COLLATE NOCASE
               OR phone3 LIKE ? COLLATE NOCASE
               OR grp LIKE ? COLLATE NOCASE
               OR designation LIKE ? COLLATE NOCASE
            ORDER BY name COLLATE NOCASE ASC
            """,
            (term,) * 9,
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


@router.get("/global", response_class=JSONResponse)
async def global_dir_list(
    q: str = "",
    session: dict = Depends(require_session),
):
    """List all global directory entries. Filter only when q has 3+ characters."""
    return await _db(_list_sync, q)


def _add_sync(conn: sqlite3.Connection, payload: dict) -> dict:
    return _insert_row(conn, payload)


@router.post("/global/add", response_class=JSONResponse)
async def global_dir_add(
    request: Request,
    session: dict = Depends(require_session),
):
    """Accept SPA multipart: text fields plus optional file uploads.

    The SPA always sends profile_pic/company_logo as filename strings; only a
    newly chosen File is treated as an upload.
    """
    form = await request.form()
    payload = _entry_payload_from_form(form)
    _validate_name_phone(payload["name"], payload["phone1"])

    profile_pic = _form_upload(form, "profile_pic")
    company_logo = _form_upload(form, "company_logo")
    payload["profile_pic"] = await _save_image(profile_pic, _profile_dir()) if profile_pic else ""
    payload["company_logo"] = await _save_image(company_logo, _logo_dir()) if company_logo else ""

    d = await _db(_add_sync, payload)
    return {"result": "success", "data": d}


@router.post("/global/delete-multiple", response_class=JSONResponse)
async def global_dir_delete_multiple(
    request: Request,
    session: dict = Depends(require_session),
):
    body = await request.json()
    ids: List[int] = [int(i) for i in body.get("ids", []) if str(i).isdigit()]
    if not ids:
        raise HTTPException(status_code=400, detail="No IDs provided")
    n = await _db(_delete_multi_sync, ids)
    return {"result": "success", "data": f"{n} entries deleted"}


@router.post("/global/upload-profile-images", response_class=JSONResponse)
async def upload_profile_images(
    session: dict = Depends(require_session),
    files: List[UploadFile] = File(...),
):
    uploaded, failed = 0, 0
    dest = _profile_dir()
    for f in files:
        try:
            await _save_image(f, dest)
            uploaded += 1
        except (HTTPException, OSError):
            failed += 1
    return {"result": "success", "uploaded": uploaded, "failed": failed}


@router.post("/global/upload-company-logos", response_class=JSONResponse)
async def upload_company_logos(
    session: dict = Depends(require_session),
    files: List[UploadFile] = File(...),
):
    uploaded, failed = 0, 0
    dest = _logo_dir()
    for f in files:
        try:
            await _save_image(f, dest)
            uploaded += 1
        except (HTTPException, OSError):
            failed += 1
    return {"result": "success", "uploaded": uploaded, "failed": failed}


@router.get("/global/image/{img_type}/{filename}")
async def serve_image(
    img_type: str,
    filename: str,
    session: dict = Depends(require_session),
):
    filename = pathlib.Path(filename).name
    if img_type == "logo":
        path = _logo_dir() / filename
    else:
        path = _profile_dir() / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path)


def _normalise_import_row(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if k is None:
            continue
        key = str(k).strip()
        alias = _HEADER_ALIASES.get(key.lower())
        if alias:
            key = alias
        out[key] = v
    return out


def _import_sync(conn: sqlite3.Connection, records: list) -> tuple:
    inserted, skipped = 0, 0
    for rec in records:
        name = (rec.get("name") or "").strip()
        phone1 = (rec.get("phone1") or "").strip()
        if not name or not phone1:
            skipped += 1
            continue
        profile_pic = re.sub(r"\s+", "_", (rec.get("profile_pic") or "").strip())
        company_logo = re.sub(r"\s+", "_", (rec.get("company_logo") or "").strip())
        _insert_row(conn, {
            "cn": rec.get("cn", "") or "",
            "name": name,
            "phone1": phone1,
            "profile_pic": profile_pic,
            "company_name": rec.get("company_name", "") or "",
            "company_logo": company_logo,
            "company_type": rec.get("company_type", "") or "",
            "group": rec.get("group", "") or "",
            "email": rec.get("email", "") or "",
            "company_address": rec.get("company_address", "") or "",
            "phone2": rec.get("phone2", "") or "",
            "phone3": rec.get("phone3", "") or "",
            "designation": rec.get("designation", "") or "",
        })
        inserted += 1
    return inserted, skipped


@router.post("/global/import", response_class=JSONResponse)
async def global_dir_import(
    session: dict = Depends(require_session),
    csv_file: UploadFile = File(...),
):
    fname = (csv_file.filename or "").lower()
    if not (fname.endswith(".csv") or fname.endswith(".txt")):
        raise HTTPException(status_code=400, detail="Please upload a valid CSV or TXT file")

    content = (await csv_file.read()).decode("utf-8-sig", errors="replace")
    records = []

    if fname.endswith(".txt"):
        for line in content.splitlines():
            d = _parse_txt_line(line)
            if d:
                records.append(d)
            elif line.strip() and not line.startswith("GD_"):
                records.append({"name": "", "phone1": ""})
    else:
        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            records.append(_normalise_import_row(row))

    inserted, skipped = await _db(_import_sync, records)
    return {
        "result": "success",
        "inserted": inserted,
        "skipped": skipped,
        "message": f"Imported {inserted} records. Skipped {skipped} invalid rows.",
    }


def _export_sync(conn: sqlite3.Connection) -> list:
    rows = conn.execute("SELECT * FROM global_directory ORDER BY id ASC").fetchall()
    return [_row_to_dict(r) for r in rows]


@router.get("/global/export")
async def global_dir_export(session: dict = Depends(require_session)):
    rows = await _db(_export_sync)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_FIELDS)
    for d in rows:
        writer.writerow([d.get(f, "") for f in _CSV_FIELDS])
    buf.seek(0)
    filename = "global_directory_export_{}.csv".format(
        datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    )
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/global/export-profile-images")
async def export_profile_images(session: dict = Depends(require_session)):
    src = _profile_dir()
    return _zip_images(src, "profile_images_export")


@router.get("/global/export-company-logos")
async def export_company_logos(session: dict = Depends(require_session)):
    src = _logo_dir()
    return _zip_images(src, "company_logos_export")


def _zip_images(src_dir: pathlib.Path, label: str) -> StreamingResponse:
    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if src_dir.is_dir():
            for f in src_dir.iterdir():
                if f.is_file() and f.suffix.lower().lstrip(".") in _IMAGE_EXTENSIONS:
                    zf.write(f, f.name)
                    count += 1
    if count == 0:
        raise HTTPException(status_code=404, detail="No images found to export")
    buf.seek(0)
    filename = "{}_{}.zip".format(label, datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))
    return StreamingResponse(
        iter([buf.read()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _update_global_sync(conn: sqlite3.Connection):
    count = conn.execute("SELECT COUNT(*) FROM global_directory").fetchone()[0]
    if count == 0:
        return 0
    _regenerate_txt(conn)
    return count


@router.post("/global/update-global", response_class=JSONResponse)
async def update_global_directory(session: dict = Depends(require_session)):
    try:
        count = await _db(_update_global_sync)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write txt file: {exc}")
    if count == 0:
        return {"result": "warning", "message": "No records found in Global Directory table"}
    return {"result": "success", "message": f"Global Directory text file regenerated ({count} records)"}


# ── Parameterised /global/{record_id} routes LAST ──────────────────────────────

def _get_sync(conn: sqlite3.Connection, record_id: int):
    row = conn.execute(
        "SELECT * FROM global_directory WHERE id = ?", (record_id,)
    ).fetchone()
    return _row_to_dict(row) if row else None


@router.get("/global/{record_id}", response_class=JSONResponse)
async def global_dir_get(
    record_id: int,
    session: dict = Depends(require_session),
):
    row = await _db(_get_sync, record_id)
    if not row:
        raise HTTPException(status_code=404, detail="Directory entry not found")
    return row


def _edit_sync(conn: sqlite3.Connection, record_id: int, payload: dict,
               new_profile: Optional[str], new_logo: Optional[str]):
    existing = conn.execute(
        "SELECT * FROM global_directory WHERE id = ?", (record_id,)
    ).fetchone()
    if not existing:
        return None
    profile_filename = new_profile if new_profile is not None else existing["profile_pic"]
    logo_filename = new_logo if new_logo is not None else existing["company_logo"]
    conn.execute(
        """
        UPDATE global_directory SET
            cn=?, name=?, phone1=?, profile_pic=?, company_name=?,
            company_logo=?, company_type=?, grp=?, email=?,
            company_address=?, phone2=?, phone3=?, designation=?
        WHERE id=?
        """,
        (
            payload["cn"], payload["name"], payload["phone1"], profile_filename,
            payload["company_name"], logo_filename, payload["company_type"],
            payload["group"], payload["email"], payload["company_address"],
            payload["phone2"], payload["phone3"], payload["designation"],
            record_id,
        ),
    )
    row = conn.execute("SELECT * FROM global_directory WHERE id = ?", (record_id,)).fetchone()
    return _row_to_dict(row)


@router.post("/global/{record_id}/edit", response_class=JSONResponse)
async def global_dir_edit(
    record_id: int,
    request: Request,
    session: dict = Depends(require_session),
):
    """Accept SPA multipart. Filename strings for profile_pic/company_logo are
    ignored (keep existing images); only a real File replaces them.
    """
    form = await request.form()
    payload = _entry_payload_from_form(form)
    _validate_name_phone(payload["name"], payload["phone1"])

    profile_pic = _form_upload(form, "profile_pic")
    company_logo = _form_upload(form, "company_logo")
    new_profile = await _save_image(profile_pic, _profile_dir()) if profile_pic else None
    new_logo = await _save_image(company_logo, _logo_dir()) if company_logo else None

    d = await _db(_edit_sync, record_id, payload, new_profile, new_logo)
    if not d:
        raise HTTPException(status_code=404, detail="Directory entry not found")
    return {"result": "success", "data": d}


def _delete_sync(conn: sqlite3.Connection, record_id: int):
    existing = conn.execute(
        "SELECT * FROM global_directory WHERE id = ?", (record_id,)
    ).fetchone()
    if not existing:
        return False
    profile_pic = existing["profile_pic"]
    logo = existing["company_logo"]
    conn.execute("DELETE FROM global_directory WHERE id = ?", (record_id,))
    _maybe_delete_image(conn, profile_pic, _profile_dir(), "profile_pic")
    _maybe_delete_image(conn, logo, _logo_dir(), "company_logo")
    return True


@router.post("/global/{record_id}/delete", response_class=JSONResponse)
async def global_dir_delete(
    record_id: int,
    session: dict = Depends(require_session),
):
    ok = await _db(_delete_sync, record_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Directory entry not found")
    return {"result": "success", "data": "Directory deleted successfully"}


def _delete_multi_sync(conn: sqlite3.Connection, ids: List[int]) -> int:
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT * FROM global_directory WHERE id IN ({placeholders})", ids
    ).fetchall()
    conn.execute(
        f"DELETE FROM global_directory WHERE id IN ({placeholders})", ids
    )
    for r in rows:
        _maybe_delete_image(conn, r["profile_pic"], _profile_dir(), "profile_pic")
        _maybe_delete_image(conn, r["company_logo"], _logo_dir(), "company_logo")
    return len(ids)
