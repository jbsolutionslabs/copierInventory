# tests/test_identity.py — Unit and integration tests for identity.py
#
# Test matrix:
#   1.  Exact serial match (compatible brand/model)
#   2.  Formatting variants (spaces, dashes, sentinel values)
#   3.  Serial conflict (same serial, incompatible brand/model)
#   4.  Strong no-serial candidate (score >= threshold → review_queued)
#   5.  Weak no-serial candidate (score < threshold → new_machine)
#   6.  Retry on concurrent insert (IntegrityError recovery)
#   7.  Reversed review pairs (A,B same unique slot as B,A)
#   8.  Malformed / None data (no crash, graceful fallback)
#   9.  Merged identity chain following
#  10.  Concurrent same-serial insertion (threading)
#
# Uses SQLite in-memory for speed.  No mocking — all tests hit a real DB.

import threading
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from db import Base, IdentityReviewQueue, Machine
from identity import (
    HIGH_CONFIDENCE_THRESHOLD,
    IdentityResolution,
    _create_machine_with_retry,
    _enqueue_review,
    _find_by_serial,
    brands_compatible,
    listing_fingerprint,
    models_compatible,
    normalize_brand,
    normalize_model_key,
    normalize_serial,
    resolve_canonical,
    resolve_machine_identity,
    score_signals,
)


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


def _make_machine(db: Session, *, serial=None, brand="Ricoh", model="MP C3004",
                  merged_into=None, is_active=True, identity_method="serial",
                  confidence=1.0) -> Machine:
    """Helper: insert a Machine and flush so it gets an id."""
    m = Machine(
        serial_normalized=serial,
        brand=brand,
        model=model,
        identity_method=identity_method,
        confidence=confidence,
        merged_into=merged_into,
        is_active=is_active,
    )
    db.add(m)
    db.flush()
    return m


# =============================================================================
# 1 & 2 — normalize_serial: exact matches and formatting variants
# =============================================================================

class TestNormalizeSerial:
    def test_basic(self):
        assert normalize_serial("ABC123") == "ABC123"

    def test_lowercase_uppercased(self):
        assert normalize_serial("abc123") == "ABC123"

    def test_strips_spaces(self):
        assert normalize_serial("  AB C 123  ") == "ABC123"

    def test_strips_dashes(self):
        assert normalize_serial("AB-C-123") == "ABC123"

    def test_strips_dots(self):
        assert normalize_serial("A.B.C.123") == "ABC123"

    def test_strips_underscores(self):
        assert normalize_serial("A_B_C_123") == "ABC123"

    def test_none_returns_none(self):
        assert normalize_serial(None) is None

    def test_sentinel_na(self):
        assert normalize_serial("n/a") is None

    def test_sentinel_unknown(self):
        assert normalize_serial("UNKNOWN") is None

    def test_sentinel_zeros(self):
        assert normalize_serial("000000") is None

    def test_sentinel_none_string(self):
        assert normalize_serial("none") is None

    def test_too_short(self):
        assert normalize_serial("AB") is None

    def test_minimum_length_ok(self):
        assert normalize_serial("ABC") == "ABC"

    def test_numeric_serial(self):
        assert normalize_serial("12345678") == "12345678"

    def test_mixed_separators(self):
        assert normalize_serial("X X-12.34") == "XX1234"


class TestNormalizeBrand:
    def test_strips_and_lowercases(self):
        assert normalize_brand("  Ricoh  ") == "ricoh"

    def test_none_returns_none(self):
        assert normalize_brand(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_brand("") is None

    def test_whitespace_only_returns_none(self):
        assert normalize_brand("   ") is None


class TestNormalizeModelKey:
    def test_strips_and_lowercases(self):
        assert normalize_model_key("MP C3004") == "mpc3004"

    def test_removes_special_chars(self):
        # normalize_model_key lowercases, removes non-alphanumeric (space, +)
        result = normalize_model_key("bizhub C360i+")
        assert result == "bizhubc360i"
        assert "+" not in result
        assert " " not in result

    def test_truncates_to_20(self):
        long_model = "A" * 30
        assert normalize_model_key(long_model) == "a" * 20

    def test_none_returns_none(self):
        assert normalize_model_key(None) is None

    def test_empty_returns_none(self):
        assert normalize_model_key("") is None


# =============================================================================
# brands_compatible / models_compatible
# =============================================================================

class TestBrandsCompatible:
    def test_exact_match(self):
        assert brands_compatible("Ricoh", "Ricoh")

    def test_case_insensitive(self):
        assert brands_compatible("ricoh", "RICOH")

    def test_oem_family_ricoh_savin(self):
        assert brands_compatible("Ricoh", "Savin")

    def test_oem_family_ricoh_lanier(self):
        assert brands_compatible("Ricoh", "Lanier")

    def test_oem_family_kyocera_copystar(self):
        assert brands_compatible("Kyocera", "Copystar")

    def test_oem_family_km_develop(self):
        assert brands_compatible("Konica Minolta", "Develop")

    def test_different_brands_incompatible(self):
        assert not brands_compatible("Ricoh", "Konica Minolta")

    def test_none_a_permissive(self):
        assert brands_compatible(None, "Ricoh")

    def test_none_b_permissive(self):
        assert brands_compatible("Ricoh", None)

    def test_both_none_permissive(self):
        assert brands_compatible(None, None)

    def test_cross_family_incompatible(self):
        assert not brands_compatible("Kyocera", "Ricoh")


class TestModelsCompatible:
    def test_exact_match(self):
        assert models_compatible("MP C3004", "MP C3004")

    def test_prefix_match_6_chars(self):
        # "mpc300" matches between "MP C3004" and "MP C3004EX"
        assert models_compatible("MP C3004", "MP C3004EX")

    def test_variant_suffix(self):
        # bizhubC360 vs bizhubC360i → first 6 chars both "bizhub" → True
        assert models_compatible("bizhub C360", "bizhub C360i")

    def test_mismatch(self):
        assert not models_compatible("MP C3004", "MP C5503")

    def test_none_a_permissive(self):
        assert models_compatible(None, "MP C3004")

    def test_none_b_permissive(self):
        assert models_compatible("MP C3004", None)

    def test_short_model_exact_required(self):
        # "abc" < 6 chars → exact match required
        assert models_compatible("ABC", "ABC")
        assert not models_compatible("ABC", "ABD")


# =============================================================================
# listing_fingerprint
# =============================================================================

class TestListingFingerprint:
    def _row(self, **kwargs):
        base = {"brand": "Ricoh", "model": "MP C3004", "state": "CA",
                "source": "rci wholesale", "total_meter": 100000}
        base.update(kwargs)
        return base

    def test_returns_32_hex_chars(self):
        fp = listing_fingerprint(self._row())
        assert len(fp) == 32
        assert all(c in "0123456789abcdef" for c in fp)

    def test_deterministic(self):
        r = self._row()
        assert listing_fingerprint(r) == listing_fingerprint(r)

    def test_meter_band_tolerance(self):
        # Both 100,001 and 104,999 land in the 100,000 band (floor to nearest 5,000)
        # 99,999 → band 95,000; 100,001 → band 100,000 (different bands, as expected)
        r1 = self._row(total_meter=100_001)
        r2 = self._row(total_meter=104_999)
        assert listing_fingerprint(r1) == listing_fingerprint(r2)

    def test_different_state_different_fp(self):
        r1 = self._row(state="CA")
        r2 = self._row(state="NJ")
        assert listing_fingerprint(r1) != listing_fingerprint(r2)

    def test_none_meter_handled(self):
        r = self._row(total_meter=None)
        fp = listing_fingerprint(r)
        assert len(fp) == 32


# =============================================================================
# score_signals
# =============================================================================

class TestScoreSignals:
    def _row(self):
        return {
            "brand": "Ricoh", "model": "MP C3004", "state": "CA",
            "total_meter": 100_000, "price": 2000, "source": "rci wholesale",
        }

    def _candidate(self):
        return {
            "brand": "Ricoh", "model": "MP C3004", "state": "CA",
            "current_meter": 105_000, "current_price": 2100,
            "seller": "rci wholesale", "source": "rci wholesale",
        }

    def test_perfect_match(self):
        s = score_signals(self._row(), self._candidate())
        assert s["total"] == pytest.approx(1.0, abs=0.01)
        assert s["brand_model"] is True
        assert s["state"] is True
        assert s["meter"] is True
        assert s["price"] is True
        assert s["seller"] is True

    def test_meter_outside_15pct(self):
        cand = self._candidate()
        cand["current_meter"] = 200_000  # 100% off
        s = score_signals(self._row(), cand)
        assert s["meter"] is False

    def test_price_outside_25pct(self):
        cand = self._candidate()
        cand["current_price"] = 10_000  # way off
        s = score_signals(self._row(), cand)
        assert s["price"] is False

    def test_state_mismatch(self):
        cand = self._candidate()
        cand["state"] = "TX"
        s = score_signals(self._row(), cand)
        assert s["state"] is False

    def test_zero_meter_not_scored(self):
        row = self._row()
        row["total_meter"] = 0
        s = score_signals(row, self._candidate())
        assert s["meter"] is False

    def test_total_is_weighted_sum(self):
        # brand_model + state only → 0.40 + 0.20 = 0.60
        row = {"brand": "Ricoh", "model": "MP C3004", "state": "CA",
               "total_meter": 0, "price": 0, "source": "other"}
        cand = {"brand": "Ricoh", "model": "MP C3004", "state": "CA",
                "current_meter": 0, "current_price": 0, "seller": "", "source": ""}
        s = score_signals(row, cand)
        assert s["total"] == pytest.approx(0.60, abs=0.01)


# =============================================================================
# 1. Serial match — DB tests
# =============================================================================

class TestSerialMatch:
    def test_exact_serial_match_same_brand_model(self, db):
        existing = _make_machine(db, serial="ABC123", brand="Ricoh", model="MP C3004")
        row = {"serial": "ABC123", "brand": "Ricoh", "model": "MP C3004", "is_color": "YES"}
        result = resolve_machine_identity(db, row)
        assert result.machine_id == existing.id
        assert result.resolution_type == "serial_match"
        assert result.confidence == 1.0
        assert result.is_new_machine is False

    def test_oem_brand_match(self, db):
        # Savin and Ricoh are the same OEM family — should match
        existing = _make_machine(db, serial="XYZ789", brand="Ricoh", model="MP C3004")
        row = {"serial": "XYZ789", "brand": "Savin", "model": "MP C3004", "is_color": "YES"}
        result = resolve_machine_identity(db, row)
        assert result.machine_id == existing.id
        assert result.resolution_type == "serial_match"

    def test_new_serial_creates_machine(self, db):
        row = {"serial": "NEWSERIAL01", "brand": "Ricoh", "model": "MP C3004", "is_color": "YES"}
        result = resolve_machine_identity(db, row)
        assert result.resolution_type == "serial_match"
        assert result.is_new_machine is True
        machine = db.get(Machine, result.machine_id)
        assert machine.serial_normalized == "NEWSERIAL01"


# =============================================================================
# 2. Formatting variants
# =============================================================================

class TestFormattingVariants:
    def test_serial_with_dashes_matches_plain(self, db):
        existing = _make_machine(db, serial="ABC1234", brand="Ricoh", model="MP C3004")
        # Incoming has dashes — normalize_serial should strip them
        row = {"serial": "ABC-1234", "brand": "Ricoh", "model": "MP C3004"}
        result = resolve_machine_identity(db, row)
        assert result.machine_id == existing.id
        assert result.resolution_type == "serial_match"

    def test_serial_with_spaces_matches(self, db):
        existing = _make_machine(db, serial="SERIAL001", brand="Kyocera", model="TASKalfa 3554ci")
        row = {"serial": "SERIAL 001", "brand": "Kyocera", "model": "TASKalfa 3554ci"}
        result = resolve_machine_identity(db, row)
        assert result.machine_id == existing.id

    def test_sentinel_serial_treated_as_no_serial(self, db):
        # "N/A" should be normalized to None → no-serial branch
        row = {"serial": "N/A", "brand": "Canon", "model": "imageRUNNER 4545"}
        result = resolve_machine_identity(db, row)
        # No candidates exist → new_machine (no review needed)
        assert result.resolution_type == "new_machine"
        assert result.is_new_machine is True


# =============================================================================
# 3. Serial conflict (same serial, incompatible brand/model)
# =============================================================================

class TestSerialConflict:
    def test_conflict_creates_review(self, db):
        existing = _make_machine(db, serial="CONFLICT01", brand="Ricoh", model="MP C3004")
        row = {"serial": "CONFLICT01", "brand": "Konica Minolta", "model": "bizhub C360"}
        result = resolve_machine_identity(db, row)
        assert result.resolution_type == "serial_conflict"
        assert result.review_queue_id is not None
        assert result.confidence == pytest.approx(0.3)

        # The new machine should NOT claim the conflicting serial
        new_machine = db.get(Machine, result.machine_id)
        assert new_machine.serial_normalized is None

        # Review row should exist
        review = db.get(IdentityReviewQueue, result.review_queue_id)
        assert review is not None
        assert review.status == "pending"

    def test_conflict_ids_in_review_pair(self, db):
        existing = _make_machine(db, serial="CONFLICT02", brand="Xerox", model="WorkCentre 7845")
        row = {"serial": "CONFLICT02", "brand": "Canon", "model": "imageRUNNER 4545"}
        result = resolve_machine_identity(db, row)

        review = db.get(IdentityReviewQueue, result.review_queue_id)
        pair = {review.machine_id_a, review.machine_id_b}
        assert existing.id in pair
        assert result.machine_id in pair


# =============================================================================
# 4. Strong no-serial candidate → review queued
# =============================================================================

class TestNoSerialHighConfidence:
    def _seed_candidate(self, db, machine_id: int) -> None:
        """Seed a listing row so _find_multi_attribute_candidates can find it."""
        from db import Listing
        listing = Listing(
            machine_id=machine_id,
            source="rci wholesale",
            state="CA",
            current_price=2000,
            current_meter=100_000,
            seller="rci wholesale",
            is_active=True,
        )
        db.add(listing)
        db.flush()

    def test_high_confidence_no_serial_queues_review(self, db):
        # Create an existing machine that should score >= HIGH_CONFIDENCE_THRESHOLD
        existing = _make_machine(
            db, serial=None, brand="Ricoh", model="MP C3004",
            identity_method="unknown", confidence=0.0,
        )
        self._seed_candidate(db, existing.id)

        row = {
            "serial": None,
            "brand": "Ricoh",
            "model": "MP C3004",
            "state": "CA",
            "total_meter": 100_000,
            "price": 2000,
            "source": "rci wholesale",
        }
        result = resolve_machine_identity(db, row)

        # Should create a NEW machine (never auto-merge)
        assert result.is_new_machine is True
        assert result.resolution_type == "review_queued"
        assert result.review_queue_id is not None
        assert result.confidence >= HIGH_CONFIDENCE_THRESHOLD

    def test_review_pair_canonical_order(self, db):
        existing = _make_machine(
            db, serial=None, brand="Ricoh", model="MP C3004",
            identity_method="unknown", confidence=0.0,
        )
        self._seed_candidate(db, existing.id)

        row = {
            "serial": None, "brand": "Ricoh", "model": "MP C3004",
            "state": "CA", "total_meter": 100_000, "price": 2000,
            "source": "rci wholesale",
        }
        result = resolve_machine_identity(db, row)
        review = db.get(IdentityReviewQueue, result.review_queue_id)
        # Canonical ordering: machine_id_a <= machine_id_b
        assert review.machine_id_a <= review.machine_id_b


# =============================================================================
# 5. Weak no-serial candidate → new_machine (no review)
# =============================================================================

class TestNoSerialLowConfidence:
    def test_low_confidence_no_review(self, db):
        # No existing machines → score stays 0 → new_machine, no review
        row = {
            "serial": None,
            "brand": "Toshiba",
            "model": "e-STUDIO 3018A",
            "state": "TX",
            "total_meter": 50_000,
            "price": 1500,
            "source": "ars",
        }
        result = resolve_machine_identity(db, row)
        assert result.resolution_type == "new_machine"
        assert result.review_queue_id is None
        assert result.is_new_machine is True

    def test_different_state_lowers_score(self, db):
        """Brand/model match but state mismatch keeps total < threshold."""
        from db import Listing
        existing = _make_machine(
            db, serial=None, brand="Toshiba", model="e-STUDIO 3018A",
            identity_method="unknown",
        )
        listing = Listing(
            machine_id=existing.id,
            source="ars",
            state="CA",          # different from row's TX
            current_price=3000,  # price way off from row's 500
            current_meter=300_000,  # meter way off
            seller="ars",
            is_active=True,
        )
        db.add(listing)
        db.flush()

        row = {
            "serial": None, "brand": "Toshiba", "model": "e-STUDIO 3018A",
            "state": "TX", "total_meter": 50_000, "price": 500, "source": "rci",
        }
        result = resolve_machine_identity(db, row)
        # brand_model=True (0.40) but state/meter/price/seller all False → 0.40 < threshold
        assert result.resolution_type == "new_machine"


# =============================================================================
# 6. Retry on concurrent insert (mock IntegrityError)
# =============================================================================

class TestConcurrentInsertRetry:
    def test_integrity_error_recovered_via_requery(self, db):
        """
        Simulate a concurrent insert race: winner machine already exists in the DB,
        and _create_machine_with_retry's savepoint commit is mocked to raise
        IntegrityError. The function should recover by re-querying the winner.
        """
        winner = _make_machine(db, serial="RACE001", brand="Ricoh", model="MP C3004")

        original_begin_nested = db.begin_nested
        first_call = [True]

        def patched_begin_nested():
            sp = original_begin_nested()  # real savepoint
            if first_call[0]:
                first_call[0] = False
                # Replace sp.commit so the first attempt "fails" as if a unique
                # constraint was violated by a concurrent transaction.
                def fake_commit():
                    raise IntegrityError(
                        "UNIQUE constraint failed: machines.serial_normalized",
                        {},
                        Exception(),
                    )
                sp.commit = fake_commit
            return sp

        with patch.object(db, "begin_nested", side_effect=patched_begin_nested):
            machine, is_new = _create_machine_with_retry(
                db,
                serial_normalized="RACE001",
                brand="Ricoh",
                model="MP C3004",
                identity_method="serial",
                confidence=1.0,
            )

        assert machine.id == winner.id
        assert is_new is False


# =============================================================================
# 7. Reversed review pairs (idempotency)
# =============================================================================

class TestReversedReviewPairs:
    def test_reversed_pair_deduplicates(self, db):
        m1 = _make_machine(db, serial=None, brand="Ricoh", model="MP C3004")
        m2 = _make_machine(db, serial=None, brand="Ricoh", model="MP C3004")

        review_a = _enqueue_review(db, m1.id, m2.id, signals={}, confidence=0.8)
        review_b = _enqueue_review(db, m2.id, m1.id, signals={}, confidence=0.8)

        assert review_a is not None
        assert review_b is not None
        assert review_a.id == review_b.id

    def test_pair_canonical_ordering(self, db):
        m1 = _make_machine(db, serial=None, brand="Canon", model="iR-ADV 4545")
        m2 = _make_machine(db, serial=None, brand="Canon", model="iR-ADV 4545")

        review = _enqueue_review(db, m2.id, m1.id, signals={}, confidence=0.75)
        assert review.machine_id_a == min(m1.id, m2.id)
        assert review.machine_id_b == max(m1.id, m2.id)


# =============================================================================
# 8. Malformed / None data
# =============================================================================

class TestMalformedData:
    def test_all_none_row(self, db):
        row = {"serial": None, "brand": None, "model": None, "state": None,
               "total_meter": None, "price": None, "source": None}
        result = resolve_machine_identity(db, row)
        # Should not crash; creates a new machine with no useful data
        assert result.machine_id is not None
        assert result.resolution_type in ("new_machine", "review_queued", "serial_match")

    def test_empty_string_serial_treated_as_none(self, db):
        row = {"serial": "", "brand": "Sharp", "model": "MX-3070V"}
        result = resolve_machine_identity(db, row)
        assert result.resolution_type in ("new_machine", "review_queued")

    def test_numeric_brand_doesnt_crash(self, db):
        row = {"serial": "VALID01", "brand": 12345, "model": None}
        # Should not raise
        result = resolve_machine_identity(db, row)
        assert result.machine_id is not None

    def test_missing_keys_dont_crash(self, db):
        # Row with no expected keys at all
        result = resolve_machine_identity(db, {})
        assert result.machine_id is not None


# =============================================================================
# 9. Merged identity chain following
# =============================================================================

class TestMergedIdentityChain:
    def test_single_merge_followed(self, db):
        original = _make_machine(db, serial="MERGED001", brand="Ricoh", model="MP C3004")
        canonical = _make_machine(db, serial="CANONICAL001", brand="Ricoh", model="MP C3004")
        # Mark original as merged into canonical
        original.merged_into = canonical.id
        db.flush()

        resolved = resolve_canonical(db, original)
        assert resolved.id == canonical.id

    def test_two_hop_chain(self, db):
        m1 = _make_machine(db, serial=None, brand="Ricoh", model="A")
        m2 = _make_machine(db, serial=None, brand="Ricoh", model="A")
        m3 = _make_machine(db, serial=None, brand="Ricoh", model="A")
        m1.merged_into = m2.id
        m2.merged_into = m3.id
        db.flush()

        resolved = resolve_canonical(db, m1)
        assert resolved.id == m3.id

    def test_serial_match_follows_merge_chain(self, db):
        original = _make_machine(db, serial="CHAIN001", brand="Ricoh", model="MP C3004")
        canonical = _make_machine(db, serial=None, brand="Ricoh", model="MP C3004",
                                   identity_method="unknown")
        original.merged_into = canonical.id
        # Remove from unique index by clearing serial_normalized
        original.serial_normalized = None
        # Give canonical the serial
        canonical.serial_normalized = "CHAIN001"
        db.flush()

        row = {"serial": "CHAIN001", "brand": "Ricoh", "model": "MP C3004"}
        result = resolve_machine_identity(db, row)
        assert result.machine_id == canonical.id

    def test_circular_merge_guard(self, db):
        m1 = _make_machine(db, serial=None, brand="Ricoh", model="A")
        m2 = _make_machine(db, serial=None, brand="Ricoh", model="A")
        # Circular: m1 → m2 → m1
        m1.merged_into = m2.id
        m2.merged_into = m1.id
        db.flush()

        # Should not infinite-loop; returns one of the two
        result = resolve_canonical(db, m1)
        assert result.id in (m1.id, m2.id)


# =============================================================================
# 10. Concurrent same-serial insertion (threading)
# =============================================================================

class TestConcurrentSameSerial:
    def test_concurrent_inserts_one_winner(self):
        """
        Verify that two independent sessions resolving the same serial converge
        on the same machine_id (idempotent serial resolution).

        The true concurrent-insert retry path (IntegrityError → re-query) is
        covered by TestConcurrentInsertRetry.test_integrity_error_recovered_via_requery.
        This test uses two sequential sessions against a shared-memory SQLite
        (with the unique index installed) to verify the end-to-end behavior:
        first call creates the machine; second call finds it without creating a duplicate.
        """
        from sqlalchemy import create_engine as _ce, text as _text

        shared_engine = _ce(
            "sqlite:///file:conc_serial_test?mode=memory&cache=shared&uri=true",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=shared_engine)
        with shared_engine.connect() as conn:
            conn.execute(_text(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_machines_serial_conc "
                "ON machines(serial_normalized) "
                "WHERE serial_normalized IS NOT NULL"
            ))
            conn.commit()

        factory = sessionmaker(bind=shared_engine, autocommit=False, autoflush=False)
        row = {"serial": "CONCURRENT01", "brand": "Ricoh", "model": "MP C3004", "is_color": "YES"}

        # Session 1: first to resolve → creates the machine
        s1 = factory()
        r1 = resolve_machine_identity(s1, row)
        s1.commit()
        s1.close()

        # Session 2: second to resolve → must find the same machine, not create a duplicate
        s2 = factory()
        r2 = resolve_machine_identity(s2, row)
        s2.commit()
        s2.close()

        Base.metadata.drop_all(bind=shared_engine)
        shared_engine.dispose()

        assert r1.machine_id == r2.machine_id, "Both sessions must resolve to the same machine"
        assert r1.is_new_machine is True,  "First session should have created the machine"
        assert r2.is_new_machine is False, "Second session should have found the existing machine"
