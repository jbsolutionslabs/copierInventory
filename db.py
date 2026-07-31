# db.py — SQLAlchemy models + session factory + migration runner

import logging
import os
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, JSON, String, Text, UniqueConstraint,
    create_engine, text,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./inventory.db")

# Railway provides postgres:// URIs; SQLAlchemy 2.x needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_is_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


# =============================================================================
# Existing tables (unchanged — do not modify column definitions here)
# =============================================================================

class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id            = Column(Integer, primary_key=True)
    started_at    = Column(DateTime, default=datetime.utcnow)
    finished_at   = Column(DateTime)
    status        = Column(String(20))   # "running" | "success" | "error"
    total_records = Column(Integer, default=0)
    new_records   = Column(Integer, default=0)
    error         = Column(Text)


class InventoryRecord(Base):
    __tablename__ = "inventory"

    id           = Column(Integer, primary_key=True)
    # --- Core OUTPUT_COLUMNS (21 fields, never altered) ---
    source       = Column(String(100))
    brand        = Column(String(100))
    model        = Column(String(200))
    condition    = Column(String(50))
    state        = Column(String(10))
    inv          = Column(String(100), index=True)
    serial       = Column(String(100), index=True)
    total_meter  = Column(Float)
    color_meter  = Column(Float)
    bw_meter     = Column(Float)
    is_color     = Column(String(5))
    feeder_model = Column(String(100))
    capacity     = Column(String(200))
    finisher     = Column(String(200))
    print_speed  = Column(String(50))
    scan         = Column(String(5))
    fax          = Column(String(5))
    qty          = Column(Float)
    price        = Column(Float)
    description  = Column(Text)
    notes        = Column(Text)
    # --- Original tracking fields (unchanged) ---
    is_new        = Column(Boolean, default=True)
    config        = Column(String(500))
    first_seen_at = Column(DateTime)
    last_seen_at  = Column(DateTime)
    scrape_run_id = Column(Integer, ForeignKey("scrape_runs.id"))
    # --- Phase 0 additions (nullable; added via SQL migration on PostgreSQL) ---
    machine_id             = Column(Integer, ForeignKey("machines.id"),           nullable=True)
    listing_id             = Column(Integer, ForeignKey("listings.id"),           nullable=True)
    last_observed_at       = Column(DateTime,                                     nullable=True)
    opportunity_score      = Column(Float,                                        nullable=True)
    score_reasons          = Column(JSON,                                         nullable=True)
    estimated_market_value = Column(Float,                                        nullable=True)
    emv_confidence         = Column(Float,                                        nullable=True)
    emv_algorithm_id       = Column(String(100), ForeignKey("algorithm_registry.id"), nullable=True)
    days_on_market         = Column(Integer,                                      nullable=True)


class WatchlistItem(Base):
    __tablename__ = "watchlist"

    id         = Column(String(36), primary_key=True)   # UUID
    # --- Original fields (unchanged) ---
    name       = Column(String(200))
    email      = Column(String(200))
    phone      = Column(String(50))
    brand      = Column(String(100))
    model      = Column(String(200))
    max_meter  = Column(Float)
    max_price  = Column(Float)
    color      = Column(String(10))
    state      = Column(String(10))
    finisher   = Column(String(10))
    fax        = Column(String(10))
    notes      = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    # --- Phase 0 additions (nullable; added via SQL migration on PostgreSQL) ---
    budget_min        = Column(Float,        nullable=True)
    urgency           = Column(String(20),   nullable=True)  # low|medium|high|critical
    financing         = Column(String(20),   nullable=True)  # cash|lease|either
    preferred_states  = Column(JSON,         nullable=True)
    alt_brands        = Column(JSON,         nullable=True)
    alt_models        = Column(JSON,         nullable=True)
    status            = Column(String(20),   nullable=True, default="active")
    match_count_total = Column(Integer,      nullable=True, default=0)
    last_match_at     = Column(DateTime,     nullable=True)
    last_notified_at  = Column(DateTime,     nullable=True)
    ai_profile        = Column(Text,         nullable=True)


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id            = Column(Integer, primary_key=True)
    filename      = Column(String(500))
    original_name = Column(String(500))
    source_key    = Column(String(50), index=True)
    size_bytes    = Column(Integer)
    storage_path  = Column(String(1000))
    uploaded_at   = Column(DateTime, default=datetime.utcnow)


# =============================================================================
# Migration tracking
# =============================================================================

class SchemaMigration(Base):
    __tablename__ = "schema_migrations"

    id         = Column(Integer, primary_key=True)
    filename   = Column(String(255), unique=True, nullable=False)
    applied_at = Column(DateTime, nullable=False, default=datetime.utcnow)


# =============================================================================
# Phase 0 — New tables
# NOTE: Column types use SQLAlchemy-portable equivalents:
#   JSON  → jsonb on PostgreSQL, TEXT on SQLite (auto-serialized)
#   DateTime → timestamp on PostgreSQL, TEXT on SQLite
# On PostgreSQL, table structure is authoritative from the SQL migration file.
# These models exist so application code can use them via ORM on both dialects.
# =============================================================================

class AlgorithmRegistry(Base):
    """Tracks every scoring/valuation algorithm version ever deployed."""
    __tablename__ = "algorithm_registry"

    id            = Column(String(100), primary_key=True)
    name          = Column(String(200), nullable=False)
    category      = Column(String(50),  nullable=False)  # scoring|valuation|analytics
    version       = Column(String(20),  nullable=False)
    description   = Column(Text)
    formula       = Column(Text)
    parameters    = Column(JSON)
    deployed_at   = Column(DateTime, nullable=False, default=datetime.utcnow)
    deprecated_at = Column(DateTime)
    is_current    = Column(Boolean, nullable=False, default=True)


class Machine(Base):
    """
    Canonical physical machine identity.
    identity_method: 'serial' | 'multi_attribute' | 'pending_review' | 'unknown'
    confidence: 1.0 for serial match, 0.65–0.85 for multi-attribute, 0.0 for unknown
    """
    __tablename__ = "machines"

    id                   = Column(Integer, primary_key=True)
    serial_normalized    = Column(String(100))
    brand                = Column(String(100))
    model                = Column(String(200))
    is_color             = Column(String(10))
    identity_method      = Column(String(20), nullable=False, default="unknown")
    confidence           = Column(Float,      nullable=False, default=0.0)
    first_observed_at    = Column(DateTime,   nullable=False, default=datetime.utcnow)
    last_observed_at     = Column(DateTime,   nullable=False, default=datetime.utcnow)
    active_listing_count = Column(Integer,    default=0)
    source_count         = Column(Integer,    default=0)
    merged_into          = Column(Integer,    ForeignKey("machines.id"), nullable=True)
    is_active            = Column(Boolean,    default=True)
    created_at           = Column(DateTime,   nullable=False, default=datetime.utcnow)


class Listing(Base):
    """
    A specific dealer's advertisement for a machine on a particular source.
    Listing identity key: (source, source_listing_id) per SOURCE_LISTING_IDENTITY config.
    consecutive_valid_misses: incremented only on valid scrape runs; reset on reappearance.
    """
    __tablename__ = "listings"

    id                       = Column(Integer, primary_key=True)
    machine_id               = Column(Integer, ForeignKey("machines.id"),   nullable=True)
    source                   = Column(String(100), nullable=False)
    source_listing_id        = Column(String(200), nullable=True)
    seller                   = Column(String(200))
    state                    = Column(String(5))
    current_price            = Column(Float)
    current_meter            = Column(Float)
    current_condition        = Column(String(50))
    current_config           = Column(Text)
    first_observed_at        = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_observed_at         = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_not_observed_at     = Column(DateTime, nullable=True)
    consecutive_valid_misses = Column(Integer,  default=0)
    possibly_missing         = Column(Boolean,  default=False)
    is_active                = Column(Boolean,  default=True)
    inventory_record_id      = Column(Integer,  ForeignKey("inventory.id"), nullable=True)
    created_at               = Column(DateTime, nullable=False, default=datetime.utcnow)


class ListingObservation(Base):
    """
    Daily snapshot of a listing's state. Written at most once per listing
    per calendar day, plus on first appearance and on removal.
    Unique constraint (listing_id, date) enforced by DB index.
    """
    __tablename__ = "listing_observations"

    id             = Column(Integer, primary_key=True)
    listing_id     = Column(Integer, ForeignKey("listings.id"),        nullable=True)
    machine_id     = Column(Integer, ForeignKey("machines.id"),        nullable=True)
    source         = Column(String(100))
    seller         = Column(String(200))
    state          = Column(String(5))
    price          = Column(Float)
    total_meter    = Column(Float)
    color_meter    = Column(Float)
    bw_meter       = Column(Float)
    condition      = Column(String(50))
    feeder_model   = Column(String(100))
    capacity       = Column(String(200))
    finisher       = Column(String(200))
    is_color       = Column(String(10))
    description    = Column(Text)
    observed_at    = Column(DateTime, nullable=False)
    scrape_run_id  = Column(Integer,  ForeignKey("scrape_runs.id"), nullable=True)
    price_changed  = Column(Boolean,  default=False)
    meter_changed  = Column(Boolean,  default=False)
    seller_changed = Column(Boolean,  default=False)
    is_first       = Column(Boolean,  default=False)


class MachineEvent(Base):
    """
    Immutable record of a meaningful state transition. Append-only.
    event_category: 'observed' | 'inferred' | 'confirmed'
    verification_type: 'scraped' | 'manual' | 'integration' | 'inferred' | 'backfilled'
    description is rendered verbatim in the UI timeline — no interpretation added.
    """
    __tablename__ = "machine_events"

    id                = Column(Integer, primary_key=True)
    machine_id        = Column(Integer, ForeignKey("machines.id"),             nullable=True)
    listing_id        = Column(Integer, ForeignKey("listings.id"),             nullable=True)
    observation_id    = Column(Integer, ForeignKey("listing_observations.id"), nullable=True)
    event_type        = Column(String(40), nullable=False)
    event_category    = Column(String(20), nullable=False, default="observed")
    source            = Column(String(100))
    seller            = Column(String(200))
    state             = Column(String(5))
    price             = Column(Float)
    total_meter       = Column(Float)
    condition         = Column(String(50))
    prev_price        = Column(Float)
    price_delta       = Column(Float)
    price_delta_pct   = Column(Float)
    prev_meter        = Column(Float)
    meter_delta       = Column(Float)
    prev_seller       = Column(String(200))
    prev_source       = Column(String(100))
    prev_state        = Column(String(5))
    description       = Column(Text)
    confidence        = Column(Float, default=1.0)
    verification_type = Column(String(20), default="scraped")
    occurred_at       = Column(DateTime, nullable=False)
    scrape_run_id     = Column(Integer,  ForeignKey("scrape_runs.id"), nullable=True)


class ComputedValue(Base):
    """
    Versioned store for all analytics and predictions.
    Every derived value records algorithm_id, confidence, and explanation.
    Superseded values are preserved (is_current=False) for reproducibility.
    """
    __tablename__ = "computed_values"

    id               = Column(Integer, primary_key=True)
    entity_type      = Column(String(30), nullable=False)   # machine|listing|market_segment|buyer
    entity_id        = Column(Integer,    nullable=False)
    value_type       = Column(String(50), nullable=False)   # opportunity_score|market_value|...
    value_category   = Column(String(20), nullable=False)   # analytics|prediction
    numeric_value    = Column(Float)
    text_value       = Column(Text)
    json_value       = Column(JSON)
    algorithm_id     = Column(String(100), ForeignKey("algorithm_registry.id"), nullable=True)
    confidence       = Column(Float,   nullable=False, default=1.0)
    explanation      = Column(Text,    nullable=False, default="")
    explanation_json = Column(JSON)
    input_snapshot   = Column(JSON)
    observation_ids  = Column(JSON)
    computed_at      = Column(DateTime, nullable=False, default=datetime.utcnow)
    valid_until      = Column(DateTime)
    superseded_by    = Column(Integer,  ForeignKey("computed_values.id"), nullable=True)
    is_current       = Column(Boolean,  default=True)


class IdentityReviewQueue(Base):
    """
    Possible machine identity matches pending human review.
    status: 'pending' | 'merged' | 'rejected' | 'uncertain'
    Never auto-merged. Reviewer must take an explicit action.
    """
    __tablename__ = "identity_review_queue"

    id            = Column(Integer,    primary_key=True)
    machine_id_a  = Column(Integer,    ForeignKey("machines.id"), nullable=False)
    machine_id_b  = Column(Integer,    ForeignKey("machines.id"), nullable=False)
    match_signals = Column(JSON,       nullable=False, default=dict)
    confidence    = Column(Float,      nullable=False)
    status        = Column(String(20), nullable=False, default="pending")
    reviewed_by   = Column(String(100))
    reviewed_at   = Column(DateTime)
    review_notes  = Column(Text)
    created_at    = Column(DateTime,   nullable=False, default=datetime.utcnow)


class IdentityAudit(Base):
    """
    Immutable log of every merge and split action.
    action: 'merge' | 'split' | 'manual_link' | 'unlink'
    """
    __tablename__ = "identity_audit"

    id                   = Column(Integer,    primary_key=True)
    action               = Column(String(20), nullable=False)
    machine_id_primary   = Column(Integer,    ForeignKey("machines.id"), nullable=True)
    machine_id_secondary = Column(Integer,    ForeignKey("machines.id"), nullable=True)
    performed_by         = Column(String(100))
    reason               = Column(Text)
    occurred_at          = Column(DateTime, nullable=False, default=datetime.utcnow)


class BuyerActivity(Base):
    """
    Event log for buyer/watchlist interactions.

    activity_type ownership:
      MATCH_IDENTIFIED  — written by scraper when a qualifying match is found
      NOTIFICATION_SENT — written by mailer after successful email delivery
    Both are idempotent per (watchlist_id, inventory_id, scrape_run_id).
    Do NOT write MATCH_IDENTIFIED from mailer.py or NOTIFICATION_SENT from scraper.py.

    Other types (written manually or by future integrations):
      PROFILE_UPDATED | INQUIRY_NOTED | BUDGET_CHANGED | PURCHASE_NOTED | STATUS_CHANGED
    """
    __tablename__ = "buyer_activity"

    id             = Column(Integer,    primary_key=True)
    watchlist_id   = Column(String(36), ForeignKey("watchlist.id"), nullable=True)
    activity_type  = Column(String(30), nullable=False)
    machine_id     = Column(Integer,    ForeignKey("machines.id"),   nullable=True)
    listing_id     = Column(Integer,    ForeignKey("listings.id"),   nullable=True)
    inventory_id   = Column(Integer,    ForeignKey("inventory.id"),  nullable=True)
    scrape_run_id  = Column(Integer,    ForeignKey("scrape_runs.id"), nullable=True)
    note           = Column(Text)
    old_value      = Column(Text)
    new_value      = Column(Text)
    occurred_at    = Column(DateTime,   nullable=False, default=datetime.utcnow)


# =============================================================================
# Migration runner
# =============================================================================

def _parse_sql_statements(sql: str) -> list[str]:
    """
    Split a DDL-only SQL file into individual executable statements.
    Strips comment lines, splits on semicolons, skips empty statements.
    Safe for CREATE TABLE, ALTER TABLE, CREATE INDEX, INSERT ... ON CONFLICT.
    Not safe for PL/pgSQL blocks (which contain internal semicolons).
    """
    # Remove full-line comments
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    cleaned = "\n".join(lines)
    # Split on semicolons
    parts = re.split(r";", cleaned)
    statements = []
    for part in parts:
        stmt = part.strip()
        if stmt:
            statements.append(stmt)
    return statements


def run_migrations(eng) -> None:
    """
    Apply pending SQL migration files from the migrations/ directory.
    Each file is applied atomically (transaction per file).
    Fatal on failure — do not swallow migration errors.
    Only called on PostgreSQL; SQLite uses create_all instead.
    """
    migrations_dir = Path(__file__).parent / "migrations"
    if not migrations_dir.exists():
        log.info("[migrations] no migrations/ directory found, skipping")
        return

    sql_files = sorted(p for p in migrations_dir.iterdir() if p.suffix == ".sql")
    if not sql_files:
        return

    with eng.connect() as conn:
        applied: set[str] = {
            row[0] for row in
            conn.execute(text("SELECT filename FROM schema_migrations"))
        }

    for sql_file in sql_files:
        if sql_file.name in applied:
            log.info("[migrations] skip %s (already applied)", sql_file.name)
            continue

        log.info("[migrations] applying %s ...", sql_file.name)
        sql = sql_file.read_text(encoding="utf-8")
        statements = _parse_sql_statements(sql)

        # Each migration file is a single atomic transaction
        with eng.begin() as conn:
            for stmt in statements:
                conn.execute(text(stmt))
            conn.execute(
                text(
                    "INSERT INTO schema_migrations (filename, applied_at) "
                    "VALUES (:f, :t) ON CONFLICT (filename) DO NOTHING"
                ),
                {"f": sql_file.name, "t": datetime.utcnow()},
            )

        log.info("[migrations] applied %s", sql_file.name)


# =============================================================================
# Seed data
# =============================================================================

_ALGORITHM_SEEDS = [
    {
        "id": "opportunity_score_v1",
        "name": "Opportunity Score",
        "category": "scoring",
        "version": "1.0.0",
        "description": (
            "Weighted 5-component score: buyer match density (30%), "
            "price vs estimated market value (25%), rarity (20%), "
            "listing freshness (15%), demand signal (10%)"
        ),
    },
    {
        "id": "market_value_v1",
        "name": "Market Value Estimation",
        "category": "valuation",
        "version": "1.0.0",
        "description": (
            "Exponentially-weighted average of observed prices. "
            "Half-life 35 days so recent prices dominate. "
            "Minimum 3 observations required; returns NULL otherwise."
        ),
    },
    {
        "id": "rarity_score_v1",
        "name": "Rarity Score",
        "category": "analytics",
        "version": "1.0.0",
        "description": (
            "Maps average simultaneous listing count over 90-day window "
            "to a 0-100 rarity scale. Adjusted upward when supply trend is falling."
        ),
    },
    {
        "id": "demand_score_v1",
        "name": "Demand Score",
        "category": "analytics",
        "version": "1.0.0",
        "description": (
            "Weighted sum of watchlist hits in last 90 days (x8), "
            "all-time watchlist hits (x2), and active buyer matches (x10). Capped at 100."
        ),
    },
    {
        "id": "comparables_v1",
        "name": "Comparables Engine",
        "category": "analytics",
        "version": "1.0.0",
        "description": (
            "Multi-signal weighted similarity: brand+model (0.40), color (0.15), "
            "meter band (0.15), price band (0.15), state/region (0.10), config (0.05). "
            "Top 10 comparables per machine stored."
        ),
    },
]


def _seed_algorithm_registry(db) -> None:
    """Insert algorithm registry seed rows. Idempotent — skips existing IDs."""
    for seed in _ALGORITHM_SEEDS:
        exists = db.query(AlgorithmRegistry).filter(
            AlgorithmRegistry.id == seed["id"]
        ).first()
        if not exists:
            db.add(AlgorithmRegistry(
                id=seed["id"],
                name=seed["name"],
                category=seed["category"],
                version=seed["version"],
                description=seed["description"],
                deployed_at=datetime.utcnow(),
                is_current=True,
            ))
    db.commit()


# =============================================================================
# Public API
# =============================================================================

def init_db() -> None:
    """
    Initialise the database:
      1. create_all() — creates any missing tables from SQLAlchemy models
         (handles new tables on both SQLite and PostgreSQL)
      2. run_migrations() — applies pending SQL migration files
         (handles new columns on existing tables; PostgreSQL only)

    SQLite note: new columns added in Phase 0 will only exist after
    deleting inventory.db and letting create_all() recreate it.
    Production (PostgreSQL) uses the SQL migration for column additions.
    """
    Base.metadata.create_all(bind=engine)
    if not _is_sqlite:
        run_migrations(engine)
    # Seed algorithm registry (idempotent; runs on both SQLite and PostgreSQL)
    db = SessionLocal()
    try:
        _seed_algorithm_registry(db)
    finally:
        db.close()


def get_db():
    """Yield a DB session; close on exit. Use as FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
