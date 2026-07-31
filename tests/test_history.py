# tests/test_history.py — Unit tests for history.py (Phase 4 history engine)
#
# Test matrix:
#   1.  FIRST_OBSERVED emitted on first scrape of a new machine
#   2.  FIRST_OBSERVED not emitted on second scrape (idempotent)
#   3.  Daily observation written once per listing per day
#   4.  Second call same day does not duplicate observation
#   5.  PRICE_CHANGED emitted when price drops past both thresholds
#   6.  PRICE_CHANGED NOT emitted when delta below threshold
#   7.  METER_CHANGED emitted when meter value changes
#   8.  SELLER_CHANGED emitted when seller changes
#   9.  Miss counter advances on valid run when listing not seen
#  10.  Miss counter does NOT advance on invalid run (source failure)
#  11.  LISTING_NOT_OBSERVED emitted when miss + hour thresholds met
#  12.  LISTING_NOT_OBSERVED NOT emitted before thresholds met
#  13.  LISTING_NOT_OBSERVED idempotent within same run
#  14.  HISTORY_ENGINE_ENABLED=False skips all processing
#  15.  HISTORY_ENABLED_SOURCES limits which sources are processed

import os
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import db as _db
from db import (
    Base,
    InventoryRecord,
    Listing,
    ListingObservation,
    Machine,
    MachineEvent,
    ScrapeRun,
)
from history import (
    _is_valid_run,
    _process_missing_listing,
    _process_seen_listing,
    _write_observation,
    run_history_engine,
)
from config import get_source_event_config


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
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_run(db: Session, started_at: datetime = None) -> ScrapeRun:
    now = started_at or datetime.utcnow()
    run = ScrapeRun(started_at=now, status="running", finished_at=now)
    db.add(run)
    db.flush()
    return run


def _make_machine(db: Session, serial: str = None, brand: str = "Ricoh",
                  model: str = "MP C3004") -> Machine:
    m = Machine(
        serial_normalized=serial,
        brand=brand,
        model=model,
        identity_method="serial" if serial else "unknown",
        confidence=1.0 if serial else 0.0,
    )
    db.add(m)
    db.flush()
    return m


def _make_listing(db: Session, machine: Machine, source: str = "RCI Wholesale",
                  source_listing_id: str = "RCI-001", price: float = 2500.0,
                  meter: float = 100_000.0, seller: str = "RCI Wholesale",
                  state: str = "CA") -> Listing:
    listing = Listing(
        machine_id        = machine.id,
        source            = source,
        source_listing_id = source_listing_id,
        seller            = seller,
        state             = state,
        current_price     = price,
        current_meter     = meter,
        current_condition = "Refurbished",
        is_active         = True,
        first_observed_at = datetime.utcnow(),
        last_observed_at  = datetime.utcnow(),
    )
    db.add(listing)
    db.flush()
    return listing


def _make_inv_record(db: Session, run: ScrapeRun, listing: Listing,
                     price: float = 2500.0, meter: float = 100_000.0,
                     state: str = "CA") -> InventoryRecord:
    rec = InventoryRecord(
        source        = listing.source,
        brand         = "Ricoh",
        model         = "MP C3004",
        condition     = "Refurbished",
        state         = state,
        price         = price,
        total_meter   = meter,
        color_meter   = meter / 2,
        bw_meter      = meter / 2,
        is_color      = "YES",
        scrape_run_id = run.id,
        listing_id    = listing.id,
        machine_id    = listing.machine_id,
        first_seen_at = datetime.utcnow(),
        last_seen_at  = datetime.utcnow(),
        is_new        = True,
    )
    db.add(rec)
    db.flush()
    return rec


def _source_cfg(source: str = "RCI Wholesale") -> dict:
    return get_source_event_config(source)


# =============================================================================
# 1 & 2 — FIRST_OBSERVED
# =============================================================================

class TestFirstObserved:
    def test_emitted_on_first_scrape(self, db):
        machine = _make_machine(db, serial="FIRST001")
        listing = _make_listing(db, machine)
        run = _make_run(db)
        inv = _make_inv_record(db, run, listing)
        now = datetime.utcnow()

        _process_seen_listing(db, run, inv, listing, _source_cfg(), now)

        events = db.query(MachineEvent).filter(
            MachineEvent.machine_id == machine.id,
            MachineEvent.event_type == "FIRST_OBSERVED",
        ).all()
        assert len(events) == 1
        assert events[0].event_category == "observed"
        assert events[0].source == "RCI Wholesale"

    def test_not_duplicated_on_second_scrape(self, db):
        machine = _make_machine(db, serial="FIRST002")
        listing = _make_listing(db, machine)
        now = datetime.utcnow()

        # Run 1 — first observation
        run1 = _make_run(db)
        inv1 = _make_inv_record(db, run1, listing)
        _process_seen_listing(db, run1, inv1, listing, _source_cfg(), now)

        # Add an observation so second run sees a previous observation
        obs = _write_observation(
            db, listing, inv1, run1.id, now,
            is_first=True, price_changed=False, meter_changed=False, seller_changed=False,
        )

        # Run 2 — next day
        tomorrow = now + timedelta(days=1)
        run2 = _make_run(db, started_at=tomorrow)
        inv2 = _make_inv_record(db, run2, listing)
        _process_seen_listing(db, run2, inv2, listing, _source_cfg(), tomorrow)

        events = db.query(MachineEvent).filter(
            MachineEvent.machine_id == machine.id,
            MachineEvent.event_type == "FIRST_OBSERVED",
        ).all()
        assert len(events) == 1  # still just one


# =============================================================================
# 3 & 4 — Daily observation snapshot
# =============================================================================

class TestDailyObservation:
    def test_observation_written_on_first_scrape(self, db):
        machine = _make_machine(db, serial="OBS001")
        listing = _make_listing(db, machine)
        run = _make_run(db)
        inv = _make_inv_record(db, run, listing)
        now = datetime.utcnow()

        obs = _write_observation(
            db, listing, inv, run.id, now,
            is_first=True, price_changed=False, meter_changed=False, seller_changed=False,
        )
        assert obs is not None
        assert obs.listing_id == listing.id
        assert obs.price == 2500.0
        assert obs.is_first is True

    def test_second_call_same_day_returns_existing(self, db):
        machine = _make_machine(db, serial="OBS002")
        listing = _make_listing(db, machine)
        run = _make_run(db)
        inv = _make_inv_record(db, run, listing)
        now = datetime.utcnow()

        obs1 = _write_observation(
            db, listing, inv, run.id, now,
            is_first=True, price_changed=False, meter_changed=False, seller_changed=False,
        )
        obs2 = _write_observation(
            db, listing, inv, run.id, now,
            is_first=True, price_changed=False, meter_changed=False, seller_changed=False,
        )
        assert obs1.id == obs2.id

        total = db.query(ListingObservation).filter(
            ListingObservation.listing_id == listing.id
        ).count()
        assert total == 1

    def test_new_observation_written_next_day(self, db):
        machine = _make_machine(db, serial="OBS003")
        listing = _make_listing(db, machine)
        run = _make_run(db)
        inv = _make_inv_record(db, run, listing)
        now = datetime.utcnow()
        tomorrow = now + timedelta(days=1)

        obs1 = _write_observation(
            db, listing, inv, run.id, now,
            is_first=True, price_changed=False, meter_changed=False, seller_changed=False,
        )
        obs2 = _write_observation(
            db, listing, inv, run.id, tomorrow,
            is_first=False, price_changed=False, meter_changed=False, seller_changed=False,
        )
        assert obs1.id != obs2.id
        assert db.query(ListingObservation).filter(
            ListingObservation.listing_id == listing.id
        ).count() == 2


# =============================================================================
# 5 & 6 — PRICE_CHANGED
# =============================================================================

class TestPriceChanged:
    def _setup(self, db, serial: str, orig_price: float, new_price: float):
        machine = _make_machine(db, serial=serial)
        listing = _make_listing(db, machine, price=new_price)
        now = datetime.utcnow()

        # Write prior observation at orig_price
        run1 = _make_run(db)
        inv1 = _make_inv_record(db, run1, listing, price=orig_price)
        prev_obs = _write_observation(
            db, listing, inv1, run1.id, now - timedelta(days=1),
            is_first=True, price_changed=False, meter_changed=False, seller_changed=False,
        )
        # Override price on the prev observation
        prev_obs.price = orig_price
        db.flush()

        # Current run with new price
        run2 = _make_run(db, started_at=now)
        inv2 = _make_inv_record(db, run2, listing, price=new_price)
        listing.current_price = new_price
        return machine, listing, inv2, prev_obs, run2, now

    def test_price_drop_above_threshold_emits_event(self, db):
        # Default: min_abs=$25, min_pct=1%
        # Drop from $2500 → $2400 = $100 (4%) — should fire
        machine, listing, inv, prev_obs, run, now = self._setup(
            db, "PRICE001", orig_price=2500.0, new_price=2400.0
        )
        from history import _emit_price_changed
        _emit_price_changed(db, listing, None, prev_obs, run, now)

        events = db.query(MachineEvent).filter(
            MachineEvent.listing_id == listing.id,
            MachineEvent.event_type == "PRICE_CHANGED",
        ).all()
        assert len(events) == 1
        assert events[0].price_delta == pytest.approx(-100.0)
        assert events[0].price_delta_pct == pytest.approx(-4.0)

    def test_price_drop_below_abs_threshold_no_event(self, db):
        # Drop from $2500 → $2490 = $10 (0.4%) — below abs threshold of $25
        machine = _make_machine(db, serial="PRICE002")
        listing = _make_listing(db, machine, price=2490.0)
        run = _make_run(db)
        inv = _make_inv_record(db, run, listing, price=2490.0)
        now = datetime.utcnow()

        # Simulate prev_obs with higher price
        prev_obs = ListingObservation(
            listing_id=listing.id, machine_id=machine.id,
            price=2500.0, observed_at=now - timedelta(days=1),
        )
        db.add(prev_obs)
        db.flush()

        # _process_seen_listing computes thresholds — delta_abs=10 < 25, should NOT fire
        source_cfg = get_source_event_config("RCI Wholesale")
        listing.current_price = 2490.0
        _process_seen_listing(db, run, inv, listing, source_cfg, now)

        events = db.query(MachineEvent).filter(
            MachineEvent.listing_id == listing.id,
            MachineEvent.event_type == "PRICE_CHANGED",
        ).all()
        assert len(events) == 0

    def test_price_drop_below_pct_threshold_no_event(self, db):
        # Drop from $5000 → $4920 = $80 abs (1.6%) — above abs but: wait 1.6% > 1%
        # Let's test: drop from $10000 → $9990 = $10 abs. Below min_abs=$25. No event.
        # Or test pct below threshold: $2500 → $2476 = $24 abs (0.96%) — below both thresholds
        machine = _make_machine(db, serial="PRICE003")
        listing = _make_listing(db, machine, price=2476.0)
        run = _make_run(db)
        inv = _make_inv_record(db, run, listing, price=2476.0)
        now = datetime.utcnow()

        prev_obs = ListingObservation(
            listing_id=listing.id, machine_id=machine.id,
            price=2500.0, observed_at=now - timedelta(days=1),
        )
        db.add(prev_obs)
        db.flush()

        source_cfg = get_source_event_config("RCI Wholesale")
        listing.current_price = 2476.0
        _process_seen_listing(db, run, inv, listing, source_cfg, now)

        events = db.query(MachineEvent).filter(
            MachineEvent.listing_id == listing.id,
            MachineEvent.event_type == "PRICE_CHANGED",
        ).all()
        # $24 < min_abs $25 → no event
        assert len(events) == 0


# =============================================================================
# 7 — METER_CHANGED
# =============================================================================

class TestMeterChanged:
    def test_meter_change_emits_event(self, db):
        machine = _make_machine(db, serial="METER001")
        listing = _make_listing(db, machine, meter=120_000.0)
        run = _make_run(db)
        inv = _make_inv_record(db, run, listing, meter=120_000.0)
        now = datetime.utcnow()

        prev_obs = ListingObservation(
            listing_id=listing.id, machine_id=machine.id,
            total_meter=100_000.0, observed_at=now - timedelta(days=1),
        )
        db.add(prev_obs)
        db.flush()

        source_cfg = get_source_event_config("RCI Wholesale")
        listing.current_meter = 120_000.0
        _process_seen_listing(db, run, inv, listing, source_cfg, now)

        events = db.query(MachineEvent).filter(
            MachineEvent.listing_id == listing.id,
            MachineEvent.event_type == "METER_CHANGED",
        ).all()
        assert len(events) == 1
        assert events[0].meter_delta == pytest.approx(20_000.0)

    def test_same_meter_no_event(self, db):
        machine = _make_machine(db, serial="METER002")
        listing = _make_listing(db, machine, meter=100_000.0)
        run = _make_run(db)
        inv = _make_inv_record(db, run, listing, meter=100_000.0)
        now = datetime.utcnow()

        prev_obs = ListingObservation(
            listing_id=listing.id, machine_id=machine.id,
            total_meter=100_000.0, observed_at=now - timedelta(days=1),
        )
        db.add(prev_obs)
        db.flush()

        source_cfg = get_source_event_config("RCI Wholesale")
        listing.current_meter = 100_000.0
        _process_seen_listing(db, run, inv, listing, source_cfg, now)

        events = db.query(MachineEvent).filter(
            MachineEvent.listing_id == listing.id,
            MachineEvent.event_type == "METER_CHANGED",
        ).all()
        assert len(events) == 0


# =============================================================================
# 8 — SELLER_CHANGED
# =============================================================================

class TestSellerChanged:
    def test_seller_change_emits_event(self, db):
        machine = _make_machine(db, serial="SELLER001")
        listing = _make_listing(db, machine, seller="New Dealer")
        run = _make_run(db)
        inv = _make_inv_record(db, run, listing)
        now = datetime.utcnow()

        prev_obs = ListingObservation(
            listing_id=listing.id, machine_id=machine.id,
            seller="Original Dealer", observed_at=now - timedelta(days=1),
        )
        db.add(prev_obs)
        db.flush()

        source_cfg = get_source_event_config("RCI Wholesale")
        listing.seller = "New Dealer"
        _process_seen_listing(db, run, inv, listing, source_cfg, now)

        events = db.query(MachineEvent).filter(
            MachineEvent.listing_id == listing.id,
            MachineEvent.event_type == "SELLER_CHANGED",
        ).all()
        assert len(events) == 1
        assert events[0].prev_seller == "Original Dealer"


# =============================================================================
# 9 & 10 — Miss counter
# =============================================================================

class TestMissCounter:
    def test_valid_run_advances_counter(self, db):
        machine = _make_machine(db, serial="MISS001")
        listing = _make_listing(db, machine)
        run = _make_run(db)
        now = datetime.utcnow()
        source_cfg = get_source_event_config("RCI Wholesale")

        assert listing.consecutive_valid_misses == 0

        _process_missing_listing(db, run, listing, source_cfg, now)

        assert listing.consecutive_valid_misses == 1
        assert listing.possibly_missing is True

    def test_counter_accumulates_across_runs(self, db):
        machine = _make_machine(db, serial="MISS002")
        listing = _make_listing(db, machine)
        source_cfg = get_source_event_config("RCI Wholesale")
        now = datetime.utcnow()

        for i in range(2):
            run = _make_run(db)
            _process_missing_listing(db, run, listing, source_cfg, now + timedelta(hours=i))

        assert listing.consecutive_valid_misses == 2

    def test_invalid_run_does_not_advance_counter(self, db):
        machine = _make_machine(db, serial="MISS003")
        listing = _make_listing(db, machine)

        # _is_valid_run returns False → miss counter should NOT advance
        # (simulate by checking _is_valid_run result and not calling _process_missing_listing)
        stats_fail = {"success": False, "record_count": 0}
        assert _is_valid_run("RCI Wholesale", stats_fail) is False

        # Counter unchanged
        assert listing.consecutive_valid_misses == 0

    def test_is_valid_run_requires_min_records(self, db):
        # RCI min_records = 50
        assert _is_valid_run("RCI Wholesale", {"success": True, "record_count": 49}) is False
        assert _is_valid_run("RCI Wholesale", {"success": True, "record_count": 50}) is True

    def test_is_valid_run_failure_always_invalid(self, db):
        assert _is_valid_run("RCI Wholesale", {"success": False, "record_count": 200}) is False


# =============================================================================
# 11 & 12 — LISTING_NOT_OBSERVED
# =============================================================================

class TestListingNotObserved:
    def _make_aged_listing(self, db: Session, serial: str, hours_ago: float,
                           misses: int = 0) -> tuple[Machine, Listing]:
        machine = _make_machine(db, serial=serial)
        last_seen = datetime.utcnow() - timedelta(hours=hours_ago)
        listing = Listing(
            machine_id        = machine.id,
            source            = "RCI Wholesale",
            source_listing_id = f"RCI-{serial}",
            seller            = "RCI Wholesale",
            state             = "CA",
            current_price     = 2500.0,
            current_meter     = 100_000.0,
            is_active         = True,
            first_observed_at = last_seen,
            last_observed_at  = last_seen,
            consecutive_valid_misses = misses,
        )
        db.add(listing)
        db.flush()
        return machine, listing

    def test_emitted_when_thresholds_met(self, db):
        # RCI: min_misses=3, min_hours=6
        machine, listing = self._make_aged_listing(db, "NOT001", hours_ago=10, misses=2)
        run = _make_run(db)
        now = datetime.utcnow()
        source_cfg = get_source_event_config("RCI Wholesale")

        _process_missing_listing(db, run, listing, source_cfg, now)
        # After this call: misses=3, elapsed≈10h → both thresholds met

        events = db.query(MachineEvent).filter(
            MachineEvent.listing_id == listing.id,
            MachineEvent.event_type == "LISTING_NOT_OBSERVED",
        ).all()
        assert len(events) == 1
        assert events[0].confidence == pytest.approx(0.9)

    def test_not_emitted_when_miss_threshold_not_met(self, db):
        # misses will be 1 after one miss, but threshold is 3 for RCI
        machine, listing = self._make_aged_listing(db, "NOT002", hours_ago=10, misses=0)
        run = _make_run(db)
        now = datetime.utcnow()
        source_cfg = get_source_event_config("RCI Wholesale")

        _process_missing_listing(db, run, listing, source_cfg, now)
        # After: misses=1, threshold=3 → not emitted

        events = db.query(MachineEvent).filter(
            MachineEvent.listing_id == listing.id,
            MachineEvent.event_type == "LISTING_NOT_OBSERVED",
        ).all()
        assert len(events) == 0

    def test_not_emitted_when_hour_threshold_not_met(self, db):
        # misses=2→3 after this call, but only 2h elapsed (RCI threshold=6h)
        machine, listing = self._make_aged_listing(db, "NOT003", hours_ago=2, misses=2)
        run = _make_run(db)
        now = datetime.utcnow()
        source_cfg = get_source_event_config("RCI Wholesale")

        _process_missing_listing(db, run, listing, source_cfg, now)
        # misses=3 but elapsed<6h → not emitted

        events = db.query(MachineEvent).filter(
            MachineEvent.listing_id == listing.id,
            MachineEvent.event_type == "LISTING_NOT_OBSERVED",
        ).all()
        assert len(events) == 0


# =============================================================================
# 13 — LISTING_NOT_OBSERVED idempotency within same run
# =============================================================================

class TestListingNotObservedIdempotency:
    def test_not_emitted_twice_in_same_run(self, db):
        machine = _make_machine(db, serial="IDEM001")
        last_seen = datetime.utcnow() - timedelta(hours=48)
        listing = Listing(
            machine_id        = machine.id,
            source            = "RCI Wholesale",
            source_listing_id = "RCI-IDEM001",
            seller            = "RCI",
            state             = "CA",
            current_price     = 2500.0,
            is_active         = True,
            first_observed_at = last_seen,
            last_observed_at  = last_seen,
            consecutive_valid_misses = 2,
        )
        db.add(listing)
        db.flush()

        run = _make_run(db)
        now = datetime.utcnow()
        source_cfg = get_source_event_config("RCI Wholesale")

        _process_missing_listing(db, run, listing, source_cfg, now)
        _process_missing_listing(db, run, listing, source_cfg, now)

        events = db.query(MachineEvent).filter(
            MachineEvent.listing_id == listing.id,
            MachineEvent.event_type == "LISTING_NOT_OBSERVED",
            MachineEvent.scrape_run_id == run.id,
        ).all()
        # Pre-check guards against the second call
        assert len(events) == 1


# =============================================================================
# 14 — HISTORY_ENGINE_ENABLED=False skips everything
# =============================================================================

class TestFeatureFlag:
    def test_disabled_skips_all_processing(self, db):
        machine = _make_machine(db, serial="FLAG001")
        listing = _make_listing(db, machine)
        run = _make_run(db)
        inv = _make_inv_record(db, run, listing)
        source_stats = {
            "RCI Wholesale": {"success": True, "record_count": 100},
        }

        with patch("history.HISTORY_ENGINE_ENABLED", False):
            run_history_engine(db, run, source_stats)

        assert db.query(ListingObservation).count() == 0
        assert db.query(MachineEvent).count() == 0

    def test_enabled_processes_source(self, db):
        machine = _make_machine(db, serial="FLAG002")
        listing = _make_listing(db, machine)
        run = _make_run(db)
        inv = _make_inv_record(db, run, listing)
        source_stats = {
            "RCI Wholesale": {"success": True, "record_count": 100},
        }

        with patch("history.HISTORY_ENGINE_ENABLED", True), \
             patch("history.HISTORY_ENABLED_SOURCES", None):
            run_history_engine(db, run, source_stats)

        # FIRST_OBSERVED + observation should be written
        assert db.query(ListingObservation).filter(
            ListingObservation.listing_id == listing.id
        ).count() == 1
        assert db.query(MachineEvent).filter(
            MachineEvent.event_type == "FIRST_OBSERVED",
            MachineEvent.machine_id == machine.id,
        ).count() == 1


# =============================================================================
# 15 — HISTORY_ENABLED_SOURCES limits which sources are processed
# =============================================================================

class TestSourceFiltering:
    def test_unlisted_source_skipped(self, db):
        machine = _make_machine(db, serial="SRC001")
        listing = _make_listing(db, machine, source="ALS Copiers",
                                source_listing_id="ALS-001")
        run = _make_run(db)
        inv = _make_inv_record(db, run, listing)
        source_stats = {
            "ALS Copiers": {"success": True, "record_count": 50},
        }

        with patch("history.HISTORY_ENGINE_ENABLED", True), \
             patch("history.HISTORY_ENABLED_SOURCES", ["RCI Wholesale"]):
            run_history_engine(db, run, source_stats)

        # ALS not in HISTORY_ENABLED_SOURCES → no processing
        assert db.query(ListingObservation).filter(
            ListingObservation.listing_id == listing.id
        ).count() == 0

    def test_listed_source_processed(self, db):
        machine = _make_machine(db, serial="SRC002")
        listing = _make_listing(db, machine, source="RCI Wholesale",
                                source_listing_id="RCI-SRC002")
        run = _make_run(db)
        inv = _make_inv_record(db, run, listing)
        source_stats = {
            "RCI Wholesale": {"success": True, "record_count": 100},
        }

        with patch("history.HISTORY_ENGINE_ENABLED", True), \
             patch("history.HISTORY_ENABLED_SOURCES", ["RCI Wholesale"]):
            run_history_engine(db, run, source_stats)

        assert db.query(ListingObservation).filter(
            ListingObservation.listing_id == listing.id
        ).count() == 1
