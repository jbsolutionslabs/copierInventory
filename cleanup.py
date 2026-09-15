# cleanup.py — Data retention: prune old history rows to keep DB size bounded.
#
# Retention policy (configurable via env vars):
#   RETENTION_OBSERVATIONS_DAYS  — listing_observations   default: 90
#   RETENTION_EVENTS_DAYS        — machine_events          default: 365
#   RETENTION_SCRAPE_RUNS_DAYS   — scrape_runs             default: 90
#   RETENTION_COMPUTED_DAYS      — computed_values (non-current) default: 30
#
# Entry point:
#   run_retention_cleanup(db) -> dict   (called by scheduler + API endpoint)

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_OBSERVATIONS_DAYS  = int(os.environ.get("RETENTION_OBSERVATIONS_DAYS", 90))
_EVENTS_DAYS        = int(os.environ.get("RETENTION_EVENTS_DAYS", 365))
_SCRAPE_RUNS_DAYS   = int(os.environ.get("RETENTION_SCRAPE_RUNS_DAYS", 90))
_COMPUTED_DAYS      = int(os.environ.get("RETENTION_COMPUTED_DAYS", 30))


def run_retention_cleanup(db: Session) -> dict:
    """
    Delete old history rows according to the retention policy.
    Returns a dict with counts of deleted rows per table.
    Safe to run concurrently — each DELETE targets rows by timestamp only.
    """
    now = datetime.utcnow()
    results: dict[str, int] = {}

    obs_cutoff     = now - timedelta(days=_OBSERVATIONS_DAYS)
    events_cutoff  = now - timedelta(days=_EVENTS_DAYS)
    runs_cutoff    = now - timedelta(days=_SCRAPE_RUNS_DAYS)
    computed_cutoff = now - timedelta(days=_COMPUTED_DAYS)

    log.info(
        "[cleanup] Retention policy: observations=%dd, events=%dd, "
        "scrape_runs=%dd, computed_values=%dd",
        _OBSERVATIONS_DAYS, _EVENTS_DAYS, _SCRAPE_RUNS_DAYS, _COMPUTED_DAYS,
    )

    # 1. listing_observations — biggest table; one row per listing per day
    try:
        r = db.execute(
            text("DELETE FROM listing_observations WHERE observed_at < :cutoff"),
            {"cutoff": obs_cutoff},
        )
        results["listing_observations"] = r.rowcount
        db.commit()
        log.info("[cleanup] listing_observations: deleted %d rows (older than %s)", r.rowcount, obs_cutoff.date())
    except Exception:
        db.rollback()
        log.exception("[cleanup] Failed to prune listing_observations")
        results["listing_observations"] = -1

    # 2. machine_events
    try:
        r = db.execute(
            text("DELETE FROM machine_events WHERE occurred_at < :cutoff"),
            {"cutoff": events_cutoff},
        )
        results["machine_events"] = r.rowcount
        db.commit()
        log.info("[cleanup] machine_events: deleted %d rows (older than %s)", r.rowcount, events_cutoff.date())
    except Exception:
        db.rollback()
        log.exception("[cleanup] Failed to prune machine_events")
        results["machine_events"] = -1

    # 3. computed_values — only non-current (superseded) rows
    try:
        r = db.execute(
            text(
                "DELETE FROM computed_values "
                "WHERE is_current = FALSE AND computed_at < :cutoff"
            ),
            {"cutoff": computed_cutoff},
        )
        results["computed_values"] = r.rowcount
        db.commit()
        log.info("[cleanup] computed_values: deleted %d superseded rows", r.rowcount)
    except Exception:
        db.rollback()
        log.exception("[cleanup] Failed to prune computed_values")
        results["computed_values"] = -1

    # 4. scrape_runs — null out FKs in child tables first to satisfy constraints
    try:
        db.execute(
            text(
                "UPDATE listing_observations SET scrape_run_id = NULL "
                "WHERE scrape_run_id IN "
                "(SELECT id FROM scrape_runs WHERE started_at < :cutoff)"
            ),
            {"cutoff": runs_cutoff},
        )
        db.execute(
            text(
                "UPDATE machine_events SET scrape_run_id = NULL "
                "WHERE scrape_run_id IN "
                "(SELECT id FROM scrape_runs WHERE started_at < :cutoff)"
            ),
            {"cutoff": runs_cutoff},
        )
        db.execute(
            text(
                "UPDATE inventory SET scrape_run_id = NULL "
                "WHERE scrape_run_id IN "
                "(SELECT id FROM scrape_runs WHERE started_at < :cutoff)"
            ),
            {"cutoff": runs_cutoff},
        )
        r = db.execute(
            text("DELETE FROM scrape_runs WHERE started_at < :cutoff"),
            {"cutoff": runs_cutoff},
        )
        results["scrape_runs"] = r.rowcount
        db.commit()
        log.info("[cleanup] scrape_runs: deleted %d rows (older than %s)", r.rowcount, runs_cutoff.date())
    except Exception:
        db.rollback()
        log.exception("[cleanup] Failed to prune scrape_runs")
        results["scrape_runs"] = -1

    # 5. VACUUM ANALYZE — reclaims disk space (PostgreSQL only; no-op on SQLite)
    try:
        db.execute(text("VACUUM ANALYZE listing_observations"))
        db.execute(text("VACUUM ANALYZE machine_events"))
        db.execute(text("VACUUM ANALYZE scrape_runs"))
        log.info("[cleanup] VACUUM ANALYZE complete.")
    except Exception:
        # VACUUM cannot run inside a transaction on PostgreSQL — that's OK,
        # the DELETEs already committed above still free space.
        log.debug("[cleanup] VACUUM skipped (expected outside transaction)")

    log.info("[cleanup] Done: %s", results)
    return results
