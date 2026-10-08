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
