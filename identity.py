# identity.py — Machine identity resolution for the market intelligence platform
#
# Design rules:
#   - Serial match  → auto-link if brand/model compatible; else review queue
#   - No serial     → never auto-merge; new machine always (high-confidence → review queue)
#   - All DB writes protected with savepoints for transactional safety
#   - Concurrent duplicate inserts handled via IntegrityError + re-query
#   - Review pairs are canonical (min_id, max_id) to prevent duplicates
#
# Public API:
#   normalize_serial(raw)         → Optional[str]
#   normalize_brand(raw)          → Optional[str]
#   normalize_model_key(raw)      → Optional[str]
#   brands_compatible(a, b)       → bool
#   models_compatible(a, b)       → bool
#   listing_fingerprint(row)      → str
#   score_signals(row, candidate) → dict
#   resolve_machine_identity(db, row) → IdentityResolution

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db import IdentityReviewQueue, Machine

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel values that indicate "no serial" in raw data
# ---------------------------------------------------------------------------
_SERIAL_SENTINELS: frozenset[str] = frozenset({
    "", "n/a", "na", "none", "null", "unknown", "tbd", "000000",
    "no serial", "no s/n", "ns", "0", "00", "000",
})

# ---------------------------------------------------------------------------
# OEM family groups — machines from these brands can share a serial number
# (e.g. Ricoh OEM'd as Savin/Lanier, Kyocera OEM'd as Copystar)
# ---------------------------------------------------------------------------
_OEM_FAMILIES: list[frozenset[str]] = [
    frozenset({"ricoh", "savin", "lanier", "gestetner", "nashuatec"}),
    frozenset({"kyocera", "copystar"}),
    frozenset({"konica minolta", "develop"}),
]


# =============================================================================
# Result type
# =============================================================================

@dataclass
class IdentityResolution:
    """
    Result of a machine identity resolution attempt.

    resolution_type values:
      'serial_match'       — matched an existing machine by serial number (compatible brand/model)
      'serial_conflict'    — same serial found but brand/model incompatible → review queue created
      'review_queued'      — no serial; high-confidence multi-attribute match → review queue
      'new_machine'        — no usable serial and no high-confidence match; new machine created
      'existing_merged'    — matched a machine that had been merged; resolved to canonical machine
    """
    machine_id: int
    resolution_type: str
    confidence: float
    signals: dict = field(default_factory=dict)
    review_queue_id: Optional[int] = None
    is_new_machine: bool = False


# =============================================================================
# Pure normalization functions (no DB access — independently unit-testable)
# =============================================================================

def normalize_serial(raw: Any) -> Optional[str]:
    """
    Return a normalized serial string, or None if the value is absent/sentinel.

    Normalization:
      1. Cast to str, strip whitespace
      2. Uppercase
      3. Remove separators: spaces, hyphens, dots, underscores
      4. Reject sentinel values
      5. Reject strings shorter than 3 characters after stripping
    """
    if raw is None:
        return None
    s = str(raw).strip().upper()
    s = re.sub(r"[\s\-\.\\_/]+", "", s)
    if s.lower() in _SERIAL_SENTINELS or len(s) < 3:
        return None
    return s


def normalize_brand(raw: Any) -> Optional[str]:
    """
    Return a lowercased, stripped brand string, or None if absent.
    Does NOT apply alias mapping — that is the caller's responsibility.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    return s if s else None


def normalize_model_key(raw: Any) -> Optional[str]:
    """
    Return a compact model key for fuzzy prefix matching.

    Normalization:
      1. Lowercase, strip
      2. Remove non-alphanumeric characters (keep letters + digits only)
      3. Take first 20 characters (prefix comparison only)
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    s = re.sub(r"[^a-z0-9]", "", s)
    return s[:20] if s else None


# =============================================================================
# Pure compatibility predicates
# =============================================================================

def brands_compatible(a: Optional[str], b: Optional[str]) -> bool:
    """
    Return True if brand strings a and b are considered the same manufacturer.

    Rules:
      - Either value being None is permissive (cannot rule out compatibility)
      - Exact normalized match → True
      - Same OEM family (e.g. Ricoh/Savin/Lanier) → True
      - Otherwise → False
    """
    if a is None or b is None:
        return True
    na = a.strip().lower()
    nb = b.strip().lower()
    if na == nb:
        return True
    for family in _OEM_FAMILIES:
        if na in family and nb in family:
            return True
    return False


def models_compatible(a: Optional[str], b: Optional[str]) -> bool:
    """
    Return True if model strings a and b are considered compatible.

    Rules:
      - Either value being None is permissive
      - Normalized model keys share a common prefix of at least 6 characters → True
      - Otherwise → False

    A 6-char prefix match is intentionally lenient to catch variants like
    "bizhub C360" vs "bizhub C360i" while avoiding false matches on short tokens.
    """
    if a is None or b is None:
        return True
    ka = normalize_model_key(a)
    kb = normalize_model_key(b)
    if not ka or not kb:
        return True
    min_len = min(len(ka), len(kb))
    if min_len < 6:
        # Short model keys: require exact match
        return ka == kb
    return ka[:6] == kb[:6]


# =============================================================================
# Listing fingerprint (for no-serial, no-stable-ID cases)
# =============================================================================

def listing_fingerprint(row: dict) -> str:
    """
    Generate a stable SHA-256 fingerprint for a listing row that has no serial
    and no source-assigned stable identifier.

    Inputs (all lowercased/normalized before hashing):
      brand, model, state, source, meter_band (meter rounded to nearest 5,000)

    Returns the first 32 hex characters of the SHA-256 digest.
    """
    brand  = str(row.get("brand")  or "").strip().lower()
    model  = str(row.get("model")  or "").strip().lower()
    state  = str(row.get("state")  or "").strip().upper()
    source = str(row.get("source") or "").strip().lower()
    meter  = float(row.get("total_meter") or 0)
    meter_band = int(meter / 5000) * 5000

    raw = f"{brand}::{model}::{state}::{source}::{meter_band}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# =============================================================================
# Signal scoring (pure — no DB)
# =============================================================================

_SIGNAL_WEIGHTS: dict[str, float] = {
    "brand_model": 0.40,
    "state":       0.20,
    "meter":       0.20,
    "price":       0.10,
    "seller":      0.10,
}

# Threshold: rows where score_signals returns >= this value are "high confidence"
HIGH_CONFIDENCE_THRESHOLD: float = 0.70


def score_signals(row: dict, candidate: dict) -> dict:
    """
    Score a row against a candidate machine dict on five signals.

    candidate dict expected keys (from a joined machines+listings query):
      brand, model, state, current_meter, current_price, seller

    Returns a dict:
      {
        "total": float,           # weighted sum ∈ [0.0, 1.0]
        "brand_model": bool,
        "state": bool,
        "meter": bool,
        "price": bool,
        "seller": bool,
      }
    """
    # Signal 1: brand + model compatibility
    sig_brand_model = brands_compatible(row.get("brand"), candidate.get("brand")) and \
                      models_compatible(row.get("model"), candidate.get("model"))

    # Signal 2: same state
    row_state = str(row.get("state") or "").strip().upper()
    cand_state = str(candidate.get("state") or "").strip().upper()
    sig_state = bool(row_state and cand_state and row_state == cand_state)

    # Signal 3: meter within ±15%
    row_meter = float(row.get("total_meter") or 0)
    cand_meter = float(candidate.get("current_meter") or 0)
    if row_meter > 0 and cand_meter > 0:
        ratio = abs(row_meter - cand_meter) / max(row_meter, cand_meter)
        sig_meter = ratio <= 0.15
    else:
        sig_meter = False

    # Signal 4: price within ±25%
    row_price = float(row.get("price") or 0)
    cand_price = float(candidate.get("current_price") or 0)
    if row_price > 0 and cand_price > 0:
        ratio = abs(row_price - cand_price) / max(row_price, cand_price)
        sig_price = ratio <= 0.25
    else:
        sig_price = False

    # Signal 5: same seller or same source
    row_seller = str(row.get("source") or "").strip().lower()
    cand_seller = str(candidate.get("seller") or "").strip().lower()
    cand_source = str(candidate.get("source") or "").strip().lower()
    sig_seller = bool(row_seller and (row_seller == cand_seller or row_seller == cand_source))

    signals = {
        "brand_model": sig_brand_model,
        "state":       sig_state,
        "meter":       sig_meter,
        "price":       sig_price,
        "seller":      sig_seller,
    }

    total = sum(_SIGNAL_WEIGHTS[k] for k, v in signals.items() if v)
    signals["total"] = round(total, 4)
    return signals


# =============================================================================
# DB helpers
# =============================================================================

def resolve_canonical(db: Session, machine: Machine) -> Machine:
    """
    Follow the merged_into chain to return the canonical (non-merged) machine.
    Guards against circular references with a depth limit of 10.
    """
    seen: set[int] = set()
    current = machine
    depth = 0
    while current.merged_into is not None and depth < 10:
        if current.merged_into in seen:
            log.warning("Circular merged_into chain detected at machine_id=%s", current.id)
            break
        seen.add(current.id)
        current = db.get(Machine, current.merged_into)
        if current is None:
            log.error("merged_into points to non-existent machine id=%s", machine.merged_into)
            break
        depth += 1
    return current


def _find_by_serial(db: Session, serial_normalized: str) -> Optional[Machine]:
    """
    Find an active machine by normalized serial. Returns None if not found.
    If the found machine is merged, follows the chain to the canonical machine.
    """
    machine = (
        db.query(Machine)
        .filter(
            Machine.serial_normalized == serial_normalized,
            Machine.merged_into.is_(None),
            Machine.is_active.is_(True),
        )
        .first()
    )
    return machine


def _create_machine_with_retry(db: Session, **kwargs) -> tuple[Machine, bool]:
    """
    Insert a new Machine row using a savepoint. If a unique constraint violation
    occurs (concurrent insert of same serial), rolls back to the savepoint and
    re-queries the winning row.

    Returns (machine, is_new) where is_new=False means another worker won the race.
    """
    serial = kwargs.get("serial_normalized")
    try:
        sp = db.begin_nested()
        machine = Machine(**kwargs)
        db.add(machine)
        db.flush()   # INSERT inside savepoint; machine.id populated before sp.commit()
        sp.commit()
        return machine, True
    except IntegrityError:
        sp.rollback()
        if serial:
            existing = _find_by_serial(db, serial)
            if existing:
                log.debug("Concurrent serial insert resolved: serial=%s machine_id=%s", serial, existing.id)
                return existing, False
        # No serial or re-query failed — unexpected; re-raise
        raise


def _enqueue_review(
    db: Session,
    id_a: int,
    id_b: int,
    signals: dict,
    confidence: float,
) -> Optional[IdentityReviewQueue]:
    """
    Insert a pending identity review for the (id_a, id_b) pair, using canonical
    ordering (min, max) to prevent duplicate entries for reversed pairs.

    If a pending review already exists for this pair, returns the existing row.
    Returns None on unexpected failure.
    """
    lo = min(id_a, id_b)
    hi = max(id_a, id_b)

    # Check for existing pending review
    existing = (
        db.query(IdentityReviewQueue)
        .filter(
            IdentityReviewQueue.machine_id_a == lo,
            IdentityReviewQueue.machine_id_b == hi,
            IdentityReviewQueue.status == "pending",
        )
        .first()
    )
    if existing:
        return existing

    try:
        sp = db.begin_nested()
        review = IdentityReviewQueue(
            machine_id_a=lo,
            machine_id_b=hi,
            match_signals=signals,
            confidence=confidence,
            status="pending",
        )
        db.add(review)
        db.flush()   # INSERT inside savepoint; review.id populated before sp.commit()
        sp.commit()
        return review
    except IntegrityError:
        sp.rollback()
        # Another worker inserted the same pair concurrently — re-query
        existing = (
            db.query(IdentityReviewQueue)
            .filter(
                IdentityReviewQueue.machine_id_a == lo,
                IdentityReviewQueue.machine_id_b == hi,
                IdentityReviewQueue.status == "pending",
            )
            .first()
        )
        return existing


def _find_multi_attribute_candidates(db: Session, row: dict) -> list[dict]:
    """
    Query active machines that share the same brand as the incoming row.
    Returns a list of candidate dicts with fields used by score_signals.

    Candidates are fetched with their most recent listing data (state, price,
    meter, seller) via a join to the listings table on machine_id.

    Only active, non-merged machines are considered.
    """
    from sqlalchemy import text

    brand_normalized = normalize_brand(row.get("brand"))
    if not brand_normalized:
        return []

    # Fetch machines by normalized brand + active listing state
    # Using raw SQL for the join to keep this readable
    sql = text("""
        SELECT
            m.id           AS machine_id,
            m.brand        AS brand,
            m.model        AS model,
            l.state        AS state,
            l.current_price AS current_price,
            l.current_meter AS current_meter,
            l.seller       AS seller,
            l.source       AS source
        FROM machines m
        LEFT JOIN listings l ON l.machine_id = m.id AND l.is_active = TRUE
        WHERE m.is_active = TRUE
          AND m.merged_into IS NULL
          AND LOWER(m.brand) = LOWER(:brand)
        LIMIT 200
    """)

    result = db.execute(sql, {"brand": brand_normalized})
    return [dict(r._mapping) for r in result]


# =============================================================================
# Main entry point
# =============================================================================

def resolve_machine_identity(db: Session, row: dict) -> IdentityResolution:
    """
    Resolve the physical machine identity for an incoming inventory row.

    Decision tree:
      1. Normalize serial from row
      2. If serial present:
           a. Find existing machine by serial
           b. If found and brand/model compatible → serial_match
           c. If found and brand/model incompatible → new machine + serial_conflict review
           d. If not found → create new machine with serial
      3. If no serial:
           a. Score all same-brand candidates
           b. If best score >= HIGH_CONFIDENCE_THRESHOLD → new machine + review_queued
           c. Otherwise → new machine (low confidence, no review)

    All DB writes use savepoints for transactional safety.
    """
    serial_raw = row.get("serial")
    serial = normalize_serial(serial_raw)
    brand = row.get("brand")
    model = row.get("model")

    # ------------------------------------------------------------------
    # Branch 1: Serial present
    # ------------------------------------------------------------------
    if serial:
        existing = _find_by_serial(db, serial)

        if existing:
            # Follow any merge chain to the canonical machine
            canonical = resolve_canonical(db, existing)
            compat = brands_compatible(brand, canonical.brand) and \
                     models_compatible(model, canonical.model)

            if compat:
                return IdentityResolution(
                    machine_id=canonical.id,
                    resolution_type="serial_match" if canonical.id == existing.id else "existing_merged",
                    confidence=1.0,
                    signals={"serial": serial, "brand_compat": True, "model_compat": True},
                    is_new_machine=False,
                )
            else:
                # Same serial, different brand/model → conflict; create new machine, queue review
                log.warning(
                    "Serial conflict: serial=%s existing_id=%s brand=%s/%s model=%s/%s",
                    serial, canonical.id, brand, canonical.brand, model, canonical.model,
                )
                new_machine, is_new = _create_machine_with_retry(
                    db,
                    serial_normalized=None,  # don't claim the conflicting serial
                    brand=brand,
                    model=model,
                    is_color=row.get("is_color"),
                    identity_method="pending_review",
                    confidence=0.3,
                )
                review = _enqueue_review(
                    db,
                    new_machine.id,
                    canonical.id,
                    signals={"serial": serial, "brand_compat": False, "model_compat": False},
                    confidence=0.3,
                )
                return IdentityResolution(
                    machine_id=new_machine.id,
                    resolution_type="serial_conflict",
                    confidence=0.3,
                    signals={"serial": serial, "conflicting_machine_id": canonical.id},
                    review_queue_id=review.id if review else None,
                    is_new_machine=is_new,
                )
        else:
            # No existing machine with this serial → create new
            new_machine, is_new = _create_machine_with_retry(
                db,
                serial_normalized=serial,
                brand=brand,
                model=model,
                is_color=row.get("is_color"),
                identity_method="serial",
                confidence=1.0,
            )
            return IdentityResolution(
                machine_id=new_machine.id,
                resolution_type="serial_match",
                confidence=1.0,
                signals={"serial": serial},
                is_new_machine=is_new,
            )

    # ------------------------------------------------------------------
    # Branch 2: No serial — score multi-attribute candidates
    # ------------------------------------------------------------------
    candidates = _find_multi_attribute_candidates(db, row)

    best_score = 0.0
    best_candidate: Optional[dict] = None
    best_signals: dict = {}

    for candidate in candidates:
        signals = score_signals(row, candidate)
        if signals["total"] > best_score:
            best_score = signals["total"]
            best_candidate = candidate
            best_signals = signals

    if best_score >= HIGH_CONFIDENCE_THRESHOLD and best_candidate is not None:
        # High-confidence candidate found → create NEW machine (never auto-merge)
        # and queue a review pair for human resolution
        new_machine, is_new = _create_machine_with_retry(
            db,
            serial_normalized=None,
            brand=brand,
            model=model,
            is_color=row.get("is_color"),
            identity_method="pending_review",
            confidence=best_score,
        )
        review = _enqueue_review(
            db,
            new_machine.id,
            int(best_candidate["machine_id"]),
            signals=best_signals,
            confidence=best_score,
        )
        return IdentityResolution(
            machine_id=new_machine.id,
            resolution_type="review_queued",
            confidence=best_score,
            signals=best_signals,
            review_queue_id=review.id if review else None,
            is_new_machine=is_new,
        )
    else:
        # Low confidence or no candidates → new machine with no review
        new_machine, is_new = _create_machine_with_retry(
            db,
            serial_normalized=None,
            brand=brand,
            model=model,
            is_color=row.get("is_color"),
            identity_method="unknown",
            confidence=best_score if best_score > 0 else 0.0,
        )
        return IdentityResolution(
            machine_id=new_machine.id,
            resolution_type="new_machine",
            confidence=best_score if best_score > 0 else 0.0,
            signals=best_signals,
            is_new_machine=is_new,
        )
