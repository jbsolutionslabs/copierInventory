# history.py — Phase 4: history engine
#
# Responsibilities:
#   - Write one ListingObservation per listing per calendar day
#   - Emit MachineEvents for meaningful state transitions
#   - Advance consecutive_valid_misses counters for unseen listings
#   - Emit LISTING_NOT_OBSERVED when per-source thresholds are met
#
# Gating:
#   - HISTORY_ENGINE_ENABLED must be True (env var)
#   - HISTORY_ENABLED_SOURCES limits to named sources when set
#
# All DB writes use savepoints. Events use pre-checks + IntegrityError fallback
# for idempotency that works on both SQLite (tests) and PostgreSQL (production).
#
# Entry point:
#   run_history_engine(db, run, source_stats)

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import (
    HISTORY_ENABLED_SOURCES,
    HISTORY_ENGINE_ENABLED,
    get_source_event_config,
)
from db import (
    InventoryRecord,
    Listing,
    ListingObservation,
    Machine,
    MachineEvent,
    ScrapeRun,
)

log = logging.getLogger(__name__)


# =============================================================================
# Public entry point
# =============================================================================

def run_history_engine(
    db: Session,
    run: ScrapeRun,
    source_stats: dict[str, dict],
) -> None:
    """
    Process history for every source that participated in this scrape run.

    source_stats keys per source:
      "success"      — bool: fetch completed without exception
      "record_count" — int: rows that reached the DB (post-dedup)

    Miss counters are only advanced for "valid" runs:
      success=True AND record_count >= SOURCE_MIN_EXPECTED_RECORDS[source]
    """
    if not HISTORY_ENGINE_ENABLED:
        return

    now = datetime.utcnow()

    for source_name, stats in source_stats.items():
        if HISTORY_ENABLED_SOURCES and source_name not in HISTORY_ENABLED_SOURCES:
            continue
        try:
            _process_source(db, run, source_name, stats, now)
            db.flush()
        except Exception:
            log.exception("History engine error for source '%s'", source_name)


# =============================================================================
# Per-source processing
# =============================================================================

def _is_valid_run(source_name: str, stats: dict) -> bool:
    """
    True when this source run is valid for advancing miss counters.
    A valid run: no exception AND returned at least SOURCE_MIN_EXPECTED_RECORDS rows.
    """
    from config import get_source_min_records
    if not stats.get("success", False):
        return False
    return stats.get("record_count", 0) >= get_source_min_records(source_name)


def _process_source(
    db: Session,
    run: ScrapeRun,
    source_name: str,
    stats: dict,
    now: datetime,
) -> None:
    """Process seen and missing listings for one source in one scrape run."""
    source_cfg = get_source_event_config(source_name)
    valid_run = _is_valid_run(source_name, stats)

    # Listings seen in this run: InventoryRecord scrape_run_id=run.id + linked Listing
    seen_pairs = (
        db.query(InventoryRecord, Listing)
        .join(Listing, InventoryRecord.listing_id == Listing.id)
        .filter(
            InventoryRecord.scrape_run_id == run.id,
            Listing.source == source_name,
        )
        .all()
    )

    seen_listing_ids: set[int] = set()
    for inv_rec, listing in seen_pairs:
        seen_listing_ids.add(listing.id)
        _process_seen_listing(db, run, inv_rec, listing, source_cfg, now)

    # Listings NOT seen — only advance counters for valid runs
    if valid_run:
        missing_q = db.query(Listing).filter(
            Listing.source == source_name,
            Listing.is_active.is_(True),
        )
        if seen_listing_ids:
            missing_q = missing_q.filter(Listing.id.notin_(seen_listing_ids))
        for listing in missing_q.all():
            _process_missing_listing(db, run, listing, source_cfg, now)


# =============================================================================
# Seen listing: observations + change events
# =============================================================================

def _process_seen_listing(
    db: Session,
    run: ScrapeRun,
    inv_rec: InventoryRecord,
    listing: Listing,
    source_cfg: dict,
    now: datetime,
) -> None:
    """Write daily observation and emit change events for a listing seen in this run."""
    # Previous observation (to detect changes)
    prev_obs = (
        db.query(ListingObservation)
        .filter(ListingObservation.listing_id == listing.id)
        .order_by(ListingObservation.observed_at.desc())
        .first()
    )

    is_first = prev_obs is None
    price_changed = False
    meter_changed = False
    seller_changed = False

    if not is_first:
        curr_price = inv_rec.price
        prev_price = prev_obs.price
        if curr_price is not None and prev_price is not None and prev_price != 0:
            delta_abs = abs(curr_price - prev_price)
            delta_pct = abs(curr_price - prev_price) / abs(prev_price) * 100
            if (delta_abs >= source_cfg["price_change_min_abs"] and
                    delta_pct >= source_cfg["price_change_min_pct"]):
                price_changed = True

        curr_meter = inv_rec.total_meter
        prev_meter = prev_obs.total_meter
        if curr_meter is not None and prev_meter is not None:
            meter_changed = curr_meter != prev_meter

        curr_seller = listing.seller or ""
        prev_seller = prev_obs.seller or ""
        seller_changed = curr_seller.strip().lower() != prev_seller.strip().lower()

    obs = _write_observation(
        db, listing, inv_rec, run.id, now,
        is_first=is_first,
        price_changed=price_changed,
        meter_changed=meter_changed,
        seller_changed=seller_changed,
    )

    # FIRST_OBSERVED fires once per machine, ever
    if is_first and listing.machine_id:
        machine = db.get(Machine, listing.machine_id)
        if machine:
            _emit_first_observed(db, machine, listing, obs, run, now)

    if price_changed and prev_obs is not None:
        _emit_price_changed(db, listing, obs, prev_obs, run, now)

    if meter_changed and prev_obs is not None:
        _emit_meter_changed(db, listing, obs, prev_obs, run, now)

    if seller_changed and prev_obs is not None:
        _emit_seller_changed(db, listing, obs, prev_obs, run, now)


# =============================================================================
# Missing listing: miss counters + LISTING_NOT_OBSERVED
# =============================================================================

def _process_missing_listing(
    db: Session,
    run: ScrapeRun,
    listing: Listing,
    source_cfg: dict,
    now: datetime,
) -> None:
    """Advance miss counter and emit LISTING_NOT_OBSERVED when thresholds are met."""
    listing.consecutive_valid_misses = (listing.consecutive_valid_misses or 0) + 1
    listing.possibly_missing = True
    listing.last_not_observed_at = now

    misses = listing.consecutive_valid_misses
    elapsed_hours = 0.0
    if listing.last_observed_at:
        elapsed_hours = (now - listing.last_observed_at).total_seconds() / 3600

    if (misses >= source_cfg["not_observed_min_misses"] and
            elapsed_hours >= source_cfg["not_observed_min_hours"]):
        _emit_listing_not_observed(db, listing, run, now, misses, elapsed_hours)


# =============================================================================
# Observation writer (idempotent: one per listing per calendar day)
# =============================================================================

def _write_observation(
    db: Session,
    listing: Listing,
    inv_rec: InventoryRecord,
    run_id: int,
    now: datetime,
    *,
    is_first: bool,
    price_changed: bool,
    meter_changed: bool,
    seller_changed: bool,
) -> Optional[ListingObservation]:
    """
    Write a ListingObservation for today. Idempotent: pre-check + IntegrityError fallback.
    Returns the observation (existing or new).
    """
    today_start = datetime.combine(now.date(), datetime.min.time())
    tomorrow_start = today_start + timedelta(days=1)

    # Pre-check: already wrote one today?
    existing = (
        db.query(ListingObservation)
        .filter(
            ListingObservation.listing_id == listing.id,
            ListingObservation.observed_at >= today_start,
            ListingObservation.observed_at < tomorrow_start,
        )
        .first()
    )
    if existing:
        return existing

    try:
        sp = db.begin_nested()
        obs = ListingObservation(
            listing_id     = listing.id,
            machine_id     = listing.machine_id,
            source         = listing.source,
            seller         = listing.seller,
            state          = inv_rec.state,
            price          = inv_rec.price,
            total_meter    = inv_rec.total_meter,
            color_meter    = inv_rec.color_meter,
            bw_meter       = inv_rec.bw_meter,
            condition      = inv_rec.condition,
            feeder_model   = inv_rec.feeder_model,
            capacity       = inv_rec.capacity,
            finisher       = inv_rec.finisher,
            is_color       = inv_rec.is_color,
            description    = inv_rec.description,
            observed_at    = now,
            scrape_run_id  = run_id,
            price_changed  = price_changed,
            meter_changed  = meter_changed,
            seller_changed = seller_changed,
            is_first       = is_first,
        )
        db.add(obs)
        db.flush()   # INSERT inside savepoint; obs.id populated before sp.commit()
        sp.commit()
        return obs
    except IntegrityError:
        sp.rollback()
        # Another worker beat us to it — re-query
        return (
            db.query(ListingObservation)
            .filter(
                ListingObservation.listing_id == listing.id,
                ListingObservation.observed_at >= today_start,
                ListingObservation.observed_at < tomorrow_start,
            )
            .first()
        )


# =============================================================================
# Event writers
# =============================================================================

def _emit_event(db: Session, **kwargs) -> Optional[MachineEvent]:
    """
    Insert a MachineEvent inside a savepoint.
    Returns the event, or None if idempotency blocked insertion.
    """
    try:
        sp = db.begin_nested()
        event = MachineEvent(**kwargs)
        db.add(event)
        db.flush()   # INSERT inside savepoint; event.id populated before sp.commit()
        sp.commit()
        return event
    except IntegrityError:
        sp.rollback()
        return None


def _emit_first_observed(
    db: Session,
    machine: Machine,
    listing: Listing,
    obs: Optional[ListingObservation],
    run: ScrapeRun,
    now: datetime,
) -> None:
    """Emit FIRST_OBSERVED for a machine. Idempotent: one ever per machine."""
    # Pre-check: already have one?
    existing = (
        db.query(MachineEvent)
        .filter(
            MachineEvent.machine_id == machine.id,
            MachineEvent.event_type == "FIRST_OBSERVED",
            MachineEvent.event_category == "observed",
        )
        .first()
    )
    if existing:
        return

    _emit_event(
        db,
        machine_id        = machine.id,
        listing_id        = listing.id,
        observation_id    = obs.id if obs else None,
        event_type        = "FIRST_OBSERVED",
        event_category    = "observed",
        source            = listing.source,
        seller            = listing.seller,
        state             = listing.state,
        price             = listing.current_price,
        total_meter       = listing.current_meter,
        condition         = listing.current_condition,
        confidence        = 1.0,
        verification_type = "scraped",
        occurred_at       = now,
        scrape_run_id     = run.id,
        description       = (
            f"First observed on {listing.source}"
            + (f" ({listing.seller})" if listing.seller and listing.seller != listing.source else "")
        ),
    )


def _emit_price_changed(
    db: Session,
    listing: Listing,
    obs: Optional[ListingObservation],
    prev_obs: ListingObservation,
    run: ScrapeRun,
    now: datetime,
) -> None:
    curr_price = listing.current_price
    prev_price = prev_obs.price
    if curr_price is None or prev_price is None:
        return
    delta = curr_price - prev_price
    delta_pct = (delta / abs(prev_price) * 100) if prev_price != 0 else 0.0

    _emit_event(
        db,
        machine_id        = listing.machine_id,
        listing_id        = listing.id,
        observation_id    = obs.id if obs else None,
        event_type        = "PRICE_CHANGED",
        event_category    = "observed",
        source            = listing.source,
        seller            = listing.seller,
        state             = listing.state,
        price             = curr_price,
        total_meter       = listing.current_meter,
        condition         = listing.current_condition,
        prev_price        = prev_price,
        price_delta       = round(delta, 2),
        price_delta_pct   = round(delta_pct, 2),
        confidence        = 1.0,
        verification_type = "scraped",
        occurred_at       = now,
        scrape_run_id     = run.id,
        description       = (
            f"Price {'dropped' if delta < 0 else 'increased'} from "
            f"${prev_price:,.0f} to ${curr_price:,.0f} ({delta_pct:+.1f}%)"
        ),
    )


def _emit_meter_changed(
    db: Session,
    listing: Listing,
    obs: Optional[ListingObservation],
    prev_obs: ListingObservation,
    run: ScrapeRun,
    now: datetime,
) -> None:
    curr_meter = listing.current_meter
    prev_meter = prev_obs.total_meter
    if curr_meter is None or prev_meter is None:
        return
    delta = curr_meter - prev_meter

    _emit_event(
        db,
        machine_id        = listing.machine_id,
        listing_id        = listing.id,
        observation_id    = obs.id if obs else None,
        event_type        = "METER_CHANGED",
        event_category    = "observed",
        source            = listing.source,
        seller            = listing.seller,
        state             = listing.state,
        price             = listing.current_price,
        total_meter       = curr_meter,
        condition         = listing.current_condition,
        prev_meter        = prev_meter,
        meter_delta       = round(delta, 0),
        confidence        = 1.0,
        verification_type = "scraped",
        occurred_at       = now,
        scrape_run_id     = run.id,
        description       = (
            f"Meter updated from {prev_meter:,.0f} to {curr_meter:,.0f} ({delta:+,.0f})"
        ),
    )


def _emit_seller_changed(
    db: Session,
    listing: Listing,
    obs: Optional[ListingObservation],
    prev_obs: ListingObservation,
    run: ScrapeRun,
    now: datetime,
) -> None:
    _emit_event(
        db,
        machine_id        = listing.machine_id,
        listing_id        = listing.id,
        observation_id    = obs.id if obs else None,
        event_type        = "SELLER_CHANGED",
        event_category    = "observed",
        source            = listing.source,
        seller            = listing.seller,
        state             = listing.state,
        price             = listing.current_price,
        total_meter       = listing.current_meter,
        condition         = listing.current_condition,
        prev_seller       = prev_obs.seller,
        confidence        = 1.0,
        verification_type = "scraped",
        occurred_at       = now,
        scrape_run_id     = run.id,
        description       = (
            f"Seller changed from '{prev_obs.seller}' to '{listing.seller}'"
        ),
    )


def _emit_listing_not_observed(
    db: Session,
    listing: Listing,
    run: ScrapeRun,
    now: datetime,
    misses: int,
    elapsed_hours: float,
) -> None:
    """Emit LISTING_NOT_OBSERVED. Idempotent per (listing, scrape_run)."""
    # Pre-check: already emitted for this listing in this run?
    existing = (
        db.query(MachineEvent)
        .filter(
            MachineEvent.listing_id == listing.id,
            MachineEvent.scrape_run_id == run.id,
            MachineEvent.event_type == "LISTING_NOT_OBSERVED",
        )
        .first()
    )
    if existing:
        return

    _emit_event(
        db,
        machine_id        = listing.machine_id,
        listing_id        = listing.id,
        observation_id    = None,
        event_type        = "LISTING_NOT_OBSERVED",
        event_category    = "observed",
        source            = listing.source,
        seller            = listing.seller,
        state             = listing.state,
        price             = listing.current_price,
        total_meter       = listing.current_meter,
        condition         = listing.current_condition,
        confidence        = 0.9,
        verification_type = "scraped",
        occurred_at       = now,
        scrape_run_id     = run.id,
        description       = (
            f"Listing not observed for {misses} consecutive valid "
            f"scrape run(s) ({elapsed_hours:.1f}h since last seen)"
        ),
    )
