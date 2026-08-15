"""
routers/directory.py — /directory/*

New in atp-dev-24. Two distinct features:

1. Global LDAP/system directory search (users + lines via ATP)
   GET  /directory          → search page (SPA)
   GET  /directory/search   → AJAX search (JSON, up to 200 results)

2. Global Directory management (contact book stored in DB + txt file)
   GET  /directory/global                      → list all entries (JSON)
   POST /directory/global/add                  → add entry (multipart)
   GET  /directory/global/{id}                 → get single entry (JSON)
   POST /directory/global/{id}/edit            → update entry (multipart)
   POST /directory/global/{id}/delete          → delete entry
   POST /directory/global/delete-multiple      → bulk delete
   POST /directory/global/upload-profile-images → bulk upload profile pics
   POST /directory/global/upload-company-logos  → bulk upload logos
   GET  /directory/global/image/{type}/{filename} → serve image
   POST /directory/global/import               → import CSV
   GET  /directory/global/export               → export CSV
   GET  /directory/global/export-profile-images → export ZIP
   GET  /directory/global/export-company-logos  → export ZIP
   POST /directory/global/update-global        → regenerate txt file

Ports: DirectoryController.php (1089 lines)
"""

import csv
import io
import os
import pathlib
import re
import zipfile
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

import atp_client
import db as db_module
from session import require_session
from logging_config import logger

router = APIRouter(prefix="/directory")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"

_MAX_RESULTS = 200
_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "bmp", "webp"}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB


# ── Paths ──────────────────────────────────────────────────────────────────────

def _global_dir_base() -> pathlib.Path:
    """Root of the global directory on disk."""
    base = os.environ.get("GLOBAL_DIR_PATH", "/home/atp/global_directory")
    return pathlib.Path(base)


def _profile_dir() -> pathlib.Path:
    return _global_dir_base() / "profile_picture"


def _logo_dir() -> pathlib.Path:
    return _global_dir_base() / "logo"


def _txt_file() -> pathlib.Path:
    return _global_dir_base() / "global_directory.txt"


# ── DB helpers ────────────────────────────────────────────────────────────────

_TABLE_CREATED = False


async def _ensure_table() -> None:
    global _TABLE_CREATED
    if _TABLE_CREATED:
        return
    pool = await db_module.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS global_directory (
                id               SERIAL PRIMARY KEY,
                cn               TEXT    NOT NULL DEFAULT '',
                name             TEXT    NOT NULL,
                phone1           TEXT    NOT NULL DEFAULT '',
                profile_pic      TEXT    NOT NULL DEFAULT '',
                company_name     TEXT    NOT NULL DEFAULT '',
                company_logo     TEXT    NOT NULL DEFAULT '',
                company_type     TEXT    NOT NULL DEFAULT '',
                grp              TEXT    NOT NULL DEFAULT '',
                email            TEXT    NOT NULL DEFAULT '',
                company_address  TEXT    NOT NULL DEFAULT '',
                phone2           TEXT    NOT NULL DEFAULT '',
                phone3           TEXT    NOT NULL DEFAULT '',
                designation      TEXT    NOT NULL DEFAULT ''
            )
        """)
    _TABLE_CREATED = True


def _row_to_dict(r) -> dict:
    return {
        "id":              r["id"],
        "cn":              r["cn"],
        "name":            r["name"],
        "phone1":          r["phone1"],
        "profile_pic":     r["profile_pic"],
        "company_name":    r["company_name"],
        "company_logo":    r["company_logo"],
        "company_type":    r["company_type"],
        "group":           r["grp"],
        "email":           r["email"],
        "company_address": r["company_address"],
        "phone2":          r["phone2"],
        "phone3":          r["phone3"],
        "designation":     r["designation"],
    }


# ── txt file helpers ───────────────────────────────────────────────────────────

def _clean_address(addr: str) -> str:
    return re.sub(r"[\r\n\t]+", " ", addr.strip())


def _format_txt_line(d: dict) -> str:
    """Format one record as a colon-delimited line matching the PHP format."""
    return "{:07d}:{}:{}:{}:{}:{}:{}:{}:{}:{}:{}:{}:{}:{}:\n".format(
        d["id"],
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


async def _regenerate_txt(conn) -> None:
    """Rewrite global_directory.txt from all current DB records."""
    rows = await conn.fetch(
        "SELECT * FROM global_directory ORDER BY id ASC"
    )
    txt = _txt_file()
    txt.parent.mkdir(parents=True, exist_ok=True)
    txt.write_text(
        "".join(_format_txt_line(_row_to_dict(r)) for r in rows),
        encoding="utf-8",
    )


def _remove_txt_entry(record_id: int) -> None:
    """Remove a single entry from the txt file by id."""
    txt = _txt_file()
    if not txt.exists():
        return
    prefix = "{:07d}:".format(record_id)
    lines = [ln for ln in txt.read_text(encoding="utf-8").splitlines(keepends=True)
             if not ln.startswith(prefix)]
    txt.write_text("".join(lines), encoding="utf-8")


def _remove_txt_entries(ids: set) -> None:
    """Remove multiple entries from the txt file."""
    txt = _txt_file()
    if not txt.exists():
        return
    lines = []
    for ln in txt.read_text(encoding="utf-8").splitlines(keepends=True):
        try:
            line_id = int(ln[:7])
        except ValueError:
            lines.append(ln)
            continue
        if line_id not in ids:
            lines.append(ln)
    txt.write_text("".join(lines), encoding="utf-8")


# ── Image helpers ─────────────────────────────────────────────────────────────

def _sanitise_filename(name: str) -> str:
    return re.sub(r"\s+", "_", pathlib.Path(name).name)


async def _save_image(upload: UploadFile, dest_dir: pathlib.Path) -> str:
    filename = _sanitise_filename(upload.filename or "upload")
    ext = pathlib.Path(filename).suffix.lower().lstrip(".")
    if ext not in _IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported image type: {ext}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    data = await upload.read()
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Image exceeds 10 MB limit")
    dest.write_bytes(data)
    return filename


# ── Index (SPA) ────────────────────────────────────────────────────────────────

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
# Global Directory management (contact book)
# ══════════════════════════════════════════════════════════════════════════════

# ── List ───────────────────────────────────────────────────────────────────────

@router.get("/global", response_class=JSONResponse)
async def global_dir_list(
    q: str = "",
    session: dict = Depends(require_session),
):
    """List all global directory entries, optionally filtered by search term."""
    await _ensure_table()
    pool = await db_module.get_pool()
    async with pool.acquire() as conn:
        if q.strip():
            term = f"%{q.strip()}%"
            rows = await conn.fetch(
                """
                SELECT * FROM global_directory
                WHERE name ILIKE $1
                   OR company_name ILIKE $1
                   OR email ILIKE $1
                   OR cn ILIKE $1
                ORDER BY name ASC
                LIMIT 200
                """,
                term,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM global_directory ORDER BY name ASC"
            )
    return [_row_to_dict(r) for r in rows]


# ── Get single ─────────────────────────────────────────────────────────────────

@router.get("/global/{record_id}", response_class=JSONResponse)
async def global_dir_get(
    record_id: int,
    session: dict = Depends(require_session),
):
    await _ensure_table()
    pool = await db_module.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM global_directory WHERE id = $1", record_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="Directory entry not found")
    return _row_to_dict(row)


# ── Add ────────────────────────────────────────────────────────────────────────

@router.post("/global/add", response_class=JSONResponse)
async def global_dir_add(
    session: dict = Depends(require_session),
    cn: str = Form(""),
    name: str = Form(...),
    phone1: str = Form(""),
    company_name: str = Form(""),
    company_type: str = Form(""),
    group: str = Form(""),
    email: str = Form(...),
    company_address: str = Form(""),
    phone2: str = Form(""),
    phone3: str = Form(""),
    designation: str = Form(""),
    profile_pic: Optional[UploadFile] = File(None),
    company_logo: Optional[UploadFile] = File(None),
):
    if not name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    if not email.strip():
        raise HTTPException(status_code=400, detail="Email is required")

    profile_filename = ""
    logo_filename = ""

    if profile_pic and profile_pic.filename:
        profile_filename = await _save_image(profile_pic, _profile_dir())

    if company_logo and company_logo.filename:
        logo_filename = await _save_image(company_logo, _logo_dir())

    await _ensure_table()
    pool = await db_module.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO global_directory
                (cn, name, phone1, profile_pic, company_name, company_logo,
                 company_type, grp, email, company_address, phone2, phone3, designation)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            RETURNING *
            """,
            cn, name, phone1, profile_filename, company_name, logo_filename,
            company_type, group, email, company_address, phone2, phone3, designation,
        )
        d = _row_to_dict(row)
        # Append to txt file
        try:
            txt = _txt_file()
            txt.parent.mkdir(parents=True, exist_ok=True)
            with txt.open("a", encoding="utf-8") as f:
                f.write(_format_txt_line(d))
        except OSError as exc:
            logger.warning("Could not update global_directory.txt: %s", exc)

    return {"result": "success", "data": d}


# ── Edit ───────────────────────────────────────────────────────────────────────

@router.post("/global/{record_id}/edit", response_class=JSONResponse)
async def global_dir_edit(
    record_id: int,
    session: dict = Depends(require_session),
    cn: str = Form(""),
    name: str = Form(...),
    phone1: str = Form(""),
    company_name: str = Form(""),
    company_type: str = Form(""),
    group: str = Form(""),
    email: str = Form(...),
    company_address: str = Form(""),
    phone2: str = Form(""),
    phone3: str = Form(""),
    designation: str = Form(""),
    profile_pic: Optional[UploadFile] = File(None),
    company_logo: Optional[UploadFile] = File(None),
):
    if not name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    if not email.strip():
        raise HTTPException(status_code=400, detail="Email is required")

    await _ensure_table()
    pool = await db_module.get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT * FROM global_directory WHERE id = $1", record_id
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Directory entry not found")

        # Handle images — keep existing if no new file uploaded
        if profile_pic and profile_pic.filename:
            profile_filename = await _save_image(profile_pic, _profile_dir())
        else:
            profile_filename = existing["profile_pic"]

        if company_logo and company_logo.filename:
            logo_filename = await _save_image(company_logo, _logo_dir())
        else:
            logo_filename = existing["company_logo"]

        row = await conn.fetchrow(
            """
            UPDATE global_directory SET
                cn=$1, name=$2, phone1=$3, profile_pic=$4, company_name=$5,
                company_logo=$6, company_type=$7, grp=$8, email=$9,
                company_address=$10, phone2=$11, phone3=$12, designation=$13
            WHERE id=$14
            RETURNING *
            """,
            cn, name, phone1, profile_filename, company_name, logo_filename,
            company_type, group, email, company_address, phone2, phone3,
            designation, record_id,
        )
        d = _row_to_dict(row)

        # Update txt file
        try:
            _remove_txt_entry(record_id)
            txt = _txt_file()
            if txt.exists():
                with txt.open("a", encoding="utf-8") as f:
                    f.write(_format_txt_line(d))
        except OSError as exc:
            logger.warning("Could not update global_directory.txt: %s", exc)

    return {"result": "success", "data": d}


# ── Delete single ──────────────────────────────────────────────────────────────

@router.post("/global/{record_id}/delete", response_class=JSONResponse)
async def global_dir_delete(
    record_id: int,
    session: dict = Depends(require_session),
):
    await _ensure_table()
    pool = await db_module.get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT * FROM global_directory WHERE id = $1", record_id
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Directory entry not found")

        profile_pic = existing["profile_pic"]
        logo = existing["company_logo"]

        await conn.execute(
            "DELETE FROM global_directory WHERE id = $1", record_id
        )

        # Remove image files if no other record references them
        for filename, img_dir in [(profile_pic, _profile_dir()), (logo, _logo_dir())]:
            if not filename:
                continue
            col = "profile_pic" if img_dir == _profile_dir() else "company_logo"
            still_used = await conn.fetchval(
                f"SELECT COUNT(*) FROM global_directory WHERE {col} = $1", filename
            )
            if still_used == 0:
                try:
                    (img_dir / filename).unlink(missing_ok=True)
                except OSError:
                    pass

    try:
        _remove_txt_entry(record_id)
    except OSError as exc:
        logger.warning("Could not update global_directory.txt: %s", exc)

    return {"result": "success", "data": "Directory deleted successfully"}


# ── Bulk delete ────────────────────────────────────────────────────────────────

@router.post("/global/delete-multiple", response_class=JSONResponse)
async def global_dir_delete_multiple(
    request: Request,
    session: dict = Depends(require_session),
):
    body = await request.json()
    ids: List[int] = [int(i) for i in body.get("ids", []) if str(i).isdigit()]
    if not ids:
        raise HTTPException(status_code=400, detail="No IDs provided")

    await _ensure_table()
    pool = await db_module.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM global_directory WHERE id = ANY($1::int[])", ids
        )
        await conn.execute(
            "DELETE FROM global_directory WHERE id = ANY($1::int[])", ids
        )

        for r in rows:
            for filename, img_dir, col in [
                (r["profile_pic"], _profile_dir(), "profile_pic"),
                (r["company_logo"], _logo_dir(), "company_logo"),
            ]:
                if not filename:
                    continue
                still_used = await conn.fetchval(
                    f"SELECT COUNT(*) FROM global_directory WHERE {col} = $1", filename
                )
                if still_used == 0:
                    try:
                        (img_dir / filename).unlink(missing_ok=True)
                    except OSError:
                        pass

    try:
        _remove_txt_entries(set(ids))
    except OSError as exc:
        logger.warning("Could not update global_directory.txt: %s", exc)

    return {"result": "success", "data": f"{len(ids)} entries deleted"}


# ── Image upload endpoints ─────────────────────────────────────────────────────

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


# ── Serve image ────────────────────────────────────────────────────────────────

@router.get("/global/image/{img_type}/{filename}")
async def serve_image(
    img_type: str,
    filename: str,
    session: dict = Depends(require_session),
):
    # Prevent path traversal
    filename = pathlib.Path(filename).name
    if img_type == "logo":
        path = _logo_dir() / filename
    else:
        path = _profile_dir() / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path)


# ── Import CSV ─────────────────────────────────────────────────────────────────

_CSV_FIELDS = [
    "cn", "name", "phone1", "profile_pic", "company_name", "company_logo",
    "company_type", "group", "email", "company_address", "phone2", "phone3",
    "designation",
]


@router.post("/global/import", response_class=JSONResponse)
async def global_dir_import(
    session: dict = Depends(require_session),
    csv_file: UploadFile = File(...),
):
    if not (csv_file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a valid CSV file")

    content = (await csv_file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(content))

    await _ensure_table()
    pool = await db_module.get_pool()
    inserted, skipped = 0, 0

    async with pool.acquire() as conn:
        for row in reader:
            name = (row.get("name") or "").strip()
            email = (row.get("email") or "").strip()
            if not name or not email:
                skipped += 1
                continue

            # Sanitise image filenames
            profile_pic = re.sub(r"\s+", "_", (row.get("profile_pic") or "").strip())
            company_logo = re.sub(r"\s+", "_", (row.get("company_logo") or "").strip())

            new_row = await conn.fetchrow(
                """
                INSERT INTO global_directory
                    (cn, name, phone1, profile_pic, company_name, company_logo,
                     company_type, grp, email, company_address, phone2, phone3, designation)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                RETURNING *
                """,
                row.get("cn", ""), name,
                row.get("phone1", ""), profile_pic,
                row.get("company_name", ""), company_logo,
                row.get("company_type", ""), row.get("group", ""),
                email, row.get("company_address", ""),
                row.get("phone2", ""), row.get("phone3", ""),
                row.get("designation", ""),
            )
            inserted += 1

            try:
                txt = _txt_file()
                txt.parent.mkdir(parents=True, exist_ok=True)
                with txt.open("a", encoding="utf-8") as f:
                    f.write(_format_txt_line(_row_to_dict(new_row)))
            except OSError:
                pass

    return {
        "result": "success",
        "inserted": inserted,
        "skipped": skipped,
        "message": f"Imported {inserted} records. Skipped {skipped} invalid rows.",
    }


# ── Export CSV ─────────────────────────────────────────────────────────────────

@router.get("/global/export")
async def global_dir_export(session: dict = Depends(require_session)):
    await _ensure_table()
    pool = await db_module.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM global_directory ORDER BY id ASC"
        )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_FIELDS)
    for r in rows:
        d = _row_to_dict(r)
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


# ── Export profile images as ZIP ───────────────────────────────────────────────

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


# ── Regenerate global_directory.txt ───────────────────────────────────────────

@router.post("/global/update-global", response_class=JSONResponse)
async def update_global_directory(session: dict = Depends(require_session)):
    await _ensure_table()
    pool = await db_module.get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM global_directory")
        if count == 0:
            return {"result": "warning", "message": "No records found in Global Directory table"}
        try:
            await _regenerate_txt(conn)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to write txt file: {exc}")

    return {"result": "success", "message": f"Global Directory text file regenerated ({count} records)"}
