# routes/watchlist.py — CRUD /api/watchlist + GET /api/watchlist/matches

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from aggregator import _find_matches
from db import InventoryRecord, ScrapeRun, WatchlistItem, get_db
from routes.inventory import _record_to_dict

router = APIRouter()


class WatchlistBulk(BaseModel):
    watchlist: list[dict] = []


class WatchlistItemIn(BaseModel):
    id:       str | None = None
    cust:     str | None = None
    name:     str | None = None   # legacy alias
    email:    str | None = None
    phone:    str | None = None
    brand:    str | None = None
    model:    str | None = None
    maxMeter: float | None = None
    maxPrice: float | None = None
    color:    str | None = None
    state:    str | None = None
    finisher: str | None = None
    fax:      str | None = None
    notes:    str | None = None


def _orm_to_dict(item: WatchlistItem) -> dict:
    return {
        "id":       item.id,
        "cust":     item.name or "",
        "email":    item.email or "",
        "phone":    item.phone or "",
        "brand":    item.brand or "",
        "model":    item.model or "",
        "maxMeter": item.max_meter,
        "maxPrice": item.max_price,
        "color":    item.color or "",
        "state":    item.state or "",
        "finisher": item.finisher or "",
        "fax":      item.fax or "",
        "notes":    item.notes or "",
        "createdAt": item.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if item.created_at else "",
    }


@router.get("/api/watchlist")
def get_watchlist(db: Session = Depends(get_db)):
    items = db.query(WatchlistItem).order_by(WatchlistItem.created_at).all()
    return {"watchlist": [_orm_to_dict(i) for i in items]}


@router.post("/api/watchlist")
def add_watchlist_item(item: WatchlistItemIn, db: Session = Depends(get_db)):
    record = WatchlistItem(
        id         = item.id or str(uuid.uuid4()),
        name       = item.cust or item.name,
        email      = item.email,
        phone      = item.phone,
        brand      = item.brand,
        model      = item.model,
        max_meter  = item.maxMeter,
        max_price  = item.maxPrice,
        color      = item.color,
        state      = item.state,
        finisher   = item.finisher,
        fax        = item.fax,
        notes      = item.notes,
        created_at = datetime.utcnow(),
    )
    db.add(record)
    db.commit()
    return {"ok": True, "item": _orm_to_dict(record)}


@router.put("/api/watchlist/{item_id}")
def update_watchlist_item(item_id: str, item: WatchlistItemIn, db: Session = Depends(get_db)):
    record = db.query(WatchlistItem).filter(WatchlistItem.id == item_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    if item.cust is not None:
        record.name = item.cust
    elif item.name is not None:
        record.name = item.name
    for field, attr in [
        ("email", "email"), ("phone", "phone"), ("brand", "brand"), ("model", "model"),
        ("color", "color"), ("state", "state"),
        ("finisher", "finisher"), ("fax", "fax"), ("notes", "notes"),
    ]:
        val = getattr(item, field)
        if val is not None:
            setattr(record, attr, val)
    if item.maxMeter is not None:
        record.max_meter = item.maxMeter
    if item.maxPrice is not None:
        record.max_price = item.maxPrice
    db.commit()
    return {"ok": True, "item": _orm_to_dict(record)}


@router.delete("/api/watchlist/{item_id}")
def delete_watchlist_item(item_id: str, db: Session = Depends(get_db)):
    record = db.query(WatchlistItem).filter(WatchlistItem.id == item_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    db.delete(record)
    db.commit()
    return {"ok": True}


@router.post("/api/watchlist/import")
def import_watchlist(items: list[dict], db: Session = Depends(get_db)):
    """Accept a raw JSON array (backup format) and replace the entire watchlist."""
    db.query(WatchlistItem).delete()
    for item_dict in items:
        record = WatchlistItem(
            id         = str(item_dict.get("id") or uuid.uuid4()),
            name       = item_dict.get("name") or item_dict.get("cust"),
            email      = item_dict.get("email"),
            phone      = item_dict.get("phone"),
            brand      = item_dict.get("brand"),
            model      = item_dict.get("model"),
            max_meter  = item_dict.get("maxMeter"),
            max_price  = item_dict.get("maxPrice"),
            color      = item_dict.get("color"),
            state      = item_dict.get("state"),
            finisher   = item_dict.get("finisher"),
            fax        = item_dict.get("fax"),
            notes      = item_dict.get("notes"),
            created_at = datetime.utcnow(),
        )
        db.add(record)
    db.commit()
    return {"ok": True, "count": len(items)}


@router.post("/api/watchlist/bulk")
def bulk_sync_watchlist(payload: WatchlistBulk, db: Session = Depends(get_db)):
    """Replace the entire watchlist with the supplied array (used by frontend sync)."""
    db.query(WatchlistItem).delete()
    for item_dict in payload.watchlist:
        record = WatchlistItem(
            id         = str(item_dict.get("id") or uuid.uuid4()),
            name       = item_dict.get("name") or item_dict.get("cust"),
            email      = item_dict.get("email"),
            phone      = item_dict.get("phone"),
            brand      = item_dict.get("brand"),
            model      = item_dict.get("model"),
            max_meter  = item_dict.get("maxMeter"),
            max_price  = item_dict.get("maxPrice"),
            color      = item_dict.get("color"),
            state      = item_dict.get("state"),
            finisher   = item_dict.get("finisher"),
            fax        = item_dict.get("fax"),
            notes      = item_dict.get("notes"),
            created_at = datetime.utcnow(),
        )
        db.add(record)
    db.commit()
    return {"ok": True, "count": len(payload.watchlist)}


@router.get("/api/watchlist/matches")
def get_watchlist_matches(db: Session = Depends(get_db)):
    """Run matching logic against current inventory for all watchlist items."""
    last_run: ScrapeRun | None = (
        db.query(ScrapeRun)
        .filter(ScrapeRun.status == "success")
        .order_by(ScrapeRun.id.desc())
        .first()
    )
    run_started_at = last_run.started_at if last_run else None

    all_records = db.query(InventoryRecord).all()
    inventory_json = [_record_to_dict(r, run_started_at) for r in all_records]
    watchlist = db.query(WatchlistItem).all()
    match_report = []

    for wl in watchlist:
        req = {
            "brand":    wl.brand or "",
            "model":    wl.model or "",
            "color":    wl.color or "",
            "state":    wl.state or "",
            "finisher": wl.finisher or "",
            "fax":      wl.fax or "",
            "maxMeter": wl.max_meter,
            "maxPrice": wl.max_price,
        }
        all_matches = _find_matches(req, inventory_json)
        if all_matches:
            match_report.append({
                "customerId":   wl.id,
                "customerName": wl.name or "",
                "email":        wl.email or "",
                "phone":        wl.phone or "",
                "matches":      all_matches[:20],
                "matchCount":   len(all_matches),
                "criteria":     {k: req.get(k) for k in ("brand", "model", "maxMeter", "maxPrice", "color", "state", "finisher", "fax")},
            })

    checked_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "checkedAt":       checked_at,
        "totalNewMatches": sum(r["matchCount"] for r in match_report),
        "requests":        match_report,
    }
