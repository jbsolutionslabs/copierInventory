# tests/test_chat.py — Tests for routes/chat.py (AI chat assistant)
#
# Uses SQLite in-memory for speed, same pattern as tests/test_identity.py.
# Route/helper functions are called directly as plain Python functions
# rather than through FastAPI's TestClient, so importing this module never
# triggers the app's lifespan (init_db / start_scheduler).

import json
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db import Base, InventoryRecord, WatchlistItem


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(scope="module")
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture()
def db(engine):
    """Provide a fresh session with rollback isolation per test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _make_inventory(db, **kwargs):
    defaults = dict(
        source="rci", brand="Canon", model="imageRUNNER 5850",
        condition="Used", state="CA", total_meter=50000, price=2000,
        is_color="NO",
    )
    defaults.update(kwargs)
    rec = InventoryRecord(**defaults)
    db.add(rec)
    db.flush()
    return rec


# =============================================================================
# _query_inventory
# =============================================================================

def test_query_inventory_filters_brand_model_max_meter(db):
    from routes.chat import _query_inventory

    _make_inventory(db, model="imageRUNNER 5850", total_meter=50000)
    _make_inventory(db, model="imageRUNNER 5850i", total_meter=60000)
    _make_inventory(db, brand="Ricoh", model="MP C3004", total_meter=30000)

    result = _query_inventory(db, brand="Canon", model_contains="5850", max_meter=55000)

    assert result["count"] == 1
    assert result["records"][0]["model"] == "imageRUNNER 5850"


def test_query_inventory_min_meter_excludes_low_units(db):
    from routes.chat import _query_inventory

    _make_inventory(db, model="imageRUNNER 5850", total_meter=50000)
    _make_inventory(db, model="imageRUNNER 5850", total_meter=20000)

    result = _query_inventory(db, brand="Canon", model_contains="5850", min_meter=25000)

    assert result["count"] == 1
    assert result["records"][0]["total"] == 50000


def test_query_inventory_condition_filter(db):
    from routes.chat import _query_inventory

    _make_inventory(db, condition="Used")
    _make_inventory(db, condition="New")

    result = _query_inventory(db, condition="New")

    assert result["count"] == 1
    assert result["records"][0]["condition"] == "New"
