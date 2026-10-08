# routes/chat.py — POST /api/chat, an AI assistant with read access to
# live inventory and read/write (no delete) access to the customer
# watchlist, backed by Claude Sonnet 5 tool use.

import json
import os

import anthropic
from anthropic import beta_tool
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from aggregator import _find_matches
from db import InventoryRecord, ScrapeRun, WatchlistItem, get_db
from routes.inventory import _record_to_dict
from routes.watchlist import (
    WatchlistItemIn,
    _orm_to_dict,
    add_watchlist_item as _wl_add_route,
    update_watchlist_item as _wl_update_route,
)

router = APIRouter()


def _query_inventory(
    db: Session,
    brand: str | None = None,
    model_contains: str | None = None,
    max_meter: float | None = None,
    min_meter: float | None = None,
    max_price: float | None = None,
    color: str | None = None,
    state: str | None = None,
    condition: str | None = None,
) -> dict:
    """Filter live inventory. Reuses aggregator._find_matches for the
    fields it already supports; min_meter and condition (which
    _find_matches doesn't support, since watchlist matching never needed
    them) are applied as additional local filtering."""
    last_run = (
        db.query(ScrapeRun)
        .filter(ScrapeRun.status == "success")
        .order_by(ScrapeRun.id.desc())
        .first()
    )
    run_started_at = last_run.started_at if last_run else None
    all_records = db.query(InventoryRecord).all()
    inventory_json = [_record_to_dict(r, run_started_at) for r in all_records]

    req = {
        "brand": brand or "",
        "model": model_contains or "",
        "color": color or "",
        "state": state or "",
        "maxMeter": max_meter,
        "maxPrice": max_price,
    }
    matches = _find_matches(req, inventory_json)

    if min_meter is not None:
        matches = [m for m in matches if (m.get("total") or 0) >= min_meter]
    if condition:
        matches = [
            m for m in matches
            if (m.get("condition") or "").lower() == condition.strip().lower()
        ]

    return {"count": len(matches), "records": matches[:200]}


def _get_watchlist(db: Session, customer_name: str | None = None) -> dict:
    items = db.query(WatchlistItem).order_by(WatchlistItem.created_at).all()
    if customer_name:
        needle = customer_name.strip().lower()
        items = [i for i in items if needle in (i.name or "").lower()]
    return {"items": [_orm_to_dict(i) for i in items]}


def _add_watchlist(
    db: Session,
    cust: str,
    email: str | None = None,
    phone: str | None = None,
    brand: str | None = None,
    model: str | None = None,
    max_meter: float | None = None,
    max_price: float | None = None,
    color: str | None = None,
    state: str | None = None,
    finisher: str | None = None,
    fax: str | None = None,
    notes: str | None = None,
) -> dict:
    item = WatchlistItemIn(
        cust=cust, email=email, phone=phone, brand=brand, model=model,
        maxMeter=max_meter, maxPrice=max_price, color=color, state=state,
        finisher=finisher, fax=fax, notes=notes,
    )
    return _wl_add_route(item, db)


def _update_watchlist(
    db: Session,
    id: str,
    email: str | None = None,
    phone: str | None = None,
    brand: str | None = None,
    model: str | None = None,
    max_meter: float | None = None,
    max_price: float | None = None,
    color: str | None = None,
    state: str | None = None,
    finisher: str | None = None,
    fax: str | None = None,
    notes: str | None = None,
) -> dict:
    item = WatchlistItemIn(
        id=id, email=email, phone=phone, brand=brand, model=model,
        maxMeter=max_meter, maxPrice=max_price, color=color, state=state,
        finisher=finisher, fax=fax, notes=notes,
    )
    return _wl_update_route(id, item, db)
