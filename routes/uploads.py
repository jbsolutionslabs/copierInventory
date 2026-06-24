# routes/uploads.py — GET/POST/DELETE /api/uploads

import os
import re
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from config import MANUAL_SOURCES
from db import UploadedFile, get_db

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "..", "imports"))

router = APIRouter()


def _safe_filename(name: str) -> str:
    """Strip path components and replace unsafe characters."""
    name = os.path.basename(name)
    name = re.sub(r"[^\w.\-]", "_", name)
    return name or "upload"


def _detect_source_key(filename: str) -> str | None:
    """
    Return the MANUAL_SOURCES key embedded in a filename, or None.
    e.g. "tnt_2024-06-01.xlsx" → "tnt", "wulff_daily.csv" → "wulff"
    Matches the same logic used by the scraper's _source_name_from_filename().
    """
    base = os.path.splitext(os.path.basename(filename))[0].lower()
    for key in MANUAL_SOURCES:
        if key in base:
            return key
    return None


def _delete_file_record(record: UploadedFile, db: Session) -> None:
    """Remove a file from disk and the DB."""
    if record.storage_path and os.path.exists(record.storage_path):
        os.remove(record.storage_path)
    db.delete(record)


@router.get("/api/uploads")
def list_uploads(db: Session = Depends(get_db)):
    files = db.query(UploadedFile).order_by(UploadedFile.uploaded_at.desc()).all()
    return {
        "files": [
            {
                "name":       f.original_name,
                "filename":   f.filename,
                "size":       f.size_bytes,
                "source":     f.source_key or "",
                "uploadedAt": f.uploaded_at.strftime("%Y-%m-%dT%H:%M:%SZ") if f.uploaded_at else "",
            }
            for f in files
        ]
    }


@router.post("/api/uploads")
async def upload_file(
    file: UploadFile = File(...),
    source_key: str = Form(default=""),   # explicit source selected by user in UI
    db: Session = Depends(get_db),
):
    original_name = file.filename or "upload"

    # Use user-supplied source_key if valid; otherwise detect from filename
    if source_key and source_key in MANUAL_SOURCES:
        resolved_key = source_key
    else:
        resolved_key = _detect_source_key(original_name)

    # Prefix the stored filename with the source key so the scraper can infer
    # the vendor from the filename (e.g. "tnt_monthly_report.xlsx")
    safe_base = _safe_filename(original_name)
    if resolved_key and not safe_base.lower().startswith(resolved_key):
        safe_name = f"{resolved_key}_{safe_base}"
    else:
        safe_name = safe_base

    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # Replace any existing file for this source so stale data doesn't accumulate
    replaced = []
    if resolved_key:
        existing = db.query(UploadedFile).filter(UploadedFile.source_key == resolved_key).all()
        for old in existing:
            replaced.append(old.original_name)
            _delete_file_record(old, db)
        db.flush()
    else:
        # Unknown source — avoid name collision
        base, ext = os.path.splitext(safe_name)
        counter = 1
        dest_path = os.path.join(UPLOAD_DIR, safe_name)
        while os.path.exists(dest_path):
            safe_name = f"{base}_{counter}{ext}"
            dest_path = os.path.join(UPLOAD_DIR, safe_name)
            counter += 1

    dest_path = os.path.join(UPLOAD_DIR, safe_name)
    content = await file.read()
    with open(dest_path, "wb") as f:
        f.write(content)

    record = UploadedFile(
        filename=safe_name,
        original_name=original_name,
        source_key=resolved_key,
        size_bytes=len(content),
        storage_path=dest_path,
        uploaded_at=datetime.utcnow(),
    )
    db.add(record)
    db.commit()

    display_name = MANUAL_SOURCES.get(resolved_key, resolved_key or "Unknown")
    result = {"ok": True, "filename": safe_name, "size": len(content),
              "sourceKey": resolved_key, "sourceName": display_name}
    if replaced:
        result["replaced"] = replaced
    return result


@router.delete("/api/uploads/{filename}")
def delete_upload(filename: str, db: Session = Depends(get_db)):
    safe_name = _safe_filename(filename)
    record = db.query(UploadedFile).filter(UploadedFile.filename == safe_name).first()
    if not record:
        raise HTTPException(status_code=404, detail="File not found")

    if os.path.exists(record.storage_path):
        os.remove(record.storage_path)

    db.delete(record)
    db.commit()
    return {"ok": True}
