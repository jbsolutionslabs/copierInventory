# routes/inventory.py — GET /api/inventory

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from db import InventoryRecord, ScrapeRun, get_db

router = APIRouter()


def _clean_num(v):
    if v is None:
        return 0
    try:
        f = float(v)
        if f != f:   # NaN check
            return 0
        return int(f) if f == int(f) else f
    except (TypeError, ValueError):
        return 0


def _record_to_dict(rec: InventoryRecord, run_started_at: datetime | None) -> dict:
    is_new = (
        rec.first_seen_at is not None
        and run_started_at is not None
        and rec.first_seen_at >= run_started_at
    )
    return {
        "vendor":      rec.source or "",
        "brand":       rec.brand or "",
        "model":       rec.model or "",
        "condition":   rec.condition or "",
        "state":       rec.state or "",
        "inv":         rec.inv or "",
        "serial":      rec.serial or "",
        "total":       _clean_num(rec.total_meter),
        "color":       _clean_num(rec.color_meter),
        "bw":          _clean_num(rec.bw_meter),
        "isColor":     rec.is_color or "",
        "feederModel": rec.feeder_model or "",
        "capacity":    rec.capacity or "",
        "finisher":    rec.finisher or "",
        "print":       rec.print_speed or "",
        "scan":        rec.scan or "",
        "fax":         rec.fax or "",
        "qty":         _clean_num(rec.qty),
        "price":       _clean_num(rec.price),
        "description": rec.description or "",
        "notes":       rec.notes or "",
        "isNew":       is_new,
        "config":      rec.config or "",
    }


@router.get("/api/inventory")
def get_inventory(db: Session = Depends(get_db)):
    # Find most recent successful scrape run for isNew baseline
    last_run: ScrapeRun | None = (
        db.query(ScrapeRun)
        .filter(ScrapeRun.status == "success")
        .order_by(ScrapeRun.id.desc())
        .first()
    )
    run_started_at = last_run.started_at if last_run else None
    prev_run: ScrapeRun | None = (
        db.query(ScrapeRun)
        .filter(ScrapeRun.status == "success", ScrapeRun.id < last_run.id)
        .order_by(ScrapeRun.id.desc())
        .first()
        if last_run
        else None
    )

    records_orm = db.query(InventoryRecord).all()
    records = [_record_to_dict(r, run_started_at) for r in records_orm]

    sources = sorted({r["vendor"] for r in records if r["vendor"]})
    brands  = sorted({r["brand"]  for r in records if r["brand"]})
    states  = sorted({r["state"]  for r in records if r["state"]})
    new_count = sum(1 for r in records if r["isNew"])

    updated   = last_run.finished_at.strftime("%Y-%m-%dT%H:%M:%SZ") if last_run and last_run.finished_at else ""
    new_since = prev_run.finished_at.strftime("%Y-%m-%dT%H:%M:%SZ") if prev_run and prev_run.finished_at else ""

    return {
        "updated":  updated,
        "newSince": new_since,
        "total":    len(records),
        "newCount": new_count,
        "sources":  sources,
        "brands":   brands,
        "states":   states,
        "records":  records,
    }
