-- =============================================================================
-- Migration 001: Market Intelligence Foundation (Phase 0)
-- Target:        PostgreSQL 14+ (Railway)
-- Safe to re-run: all statements use IF NOT EXISTS / ON CONFLICT DO NOTHING
-- Contains:      DDL only — no procedural SQL, no functions, no triggers
-- Applied by:    db.run_migrations() — DO NOT run manually against production
-- =============================================================================


-- =============================================================================
-- 1. Algorithm Registry
--    Tracks every scoring/valuation algorithm version ever deployed.
--    Seed entries for all v1 algorithms used in Phase 0–2.
-- =============================================================================

CREATE TABLE IF NOT EXISTS algorithm_registry (
    id            VARCHAR(100) PRIMARY KEY,
    name          VARCHAR(200) NOT NULL,
    category      VARCHAR(50)  NOT NULL,
    version       VARCHAR(20)  NOT NULL,
    description   TEXT,
    formula       TEXT,
    parameters    TEXT,
    deployed_at   TIMESTAMP    NOT NULL DEFAULT NOW(),
    deprecated_at TIMESTAMP,
    is_current    BOOLEAN      NOT NULL DEFAULT TRUE
);

-- Seed data for algorithm_registry is applied via db.py::_seed_algorithm_registry()
-- to avoid semicolons inside string literals confusing the SQL statement parser.


-- =============================================================================
-- 2. Machines
--    Canonical physical machine identity. One row per unique piece of equipment.
--    Created by identity resolution in scraper Phase 3.
-- =============================================================================

CREATE TABLE IF NOT EXISTS machines (
    id                   SERIAL       PRIMARY KEY,
    serial_normalized    VARCHAR(100),
    brand                VARCHAR(100),
    model                VARCHAR(200),
    is_color             VARCHAR(10),
    -- Identity confidence
    identity_method      VARCHAR(20)  NOT NULL DEFAULT 'unknown',
    confidence           FLOAT        NOT NULL DEFAULT 0.0,
    -- Denormalized summary (updated by analytics job)
    first_observed_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
    last_observed_at     TIMESTAMP    NOT NULL DEFAULT NOW(),
    active_listing_count INTEGER               DEFAULT 0,
    source_count         INTEGER               DEFAULT 0,
    -- Identity management
    merged_into          INTEGER      REFERENCES machines(id),
    is_active            BOOLEAN               DEFAULT TRUE,
    created_at           TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- Serial uniqueness: only one active identity per normalized serial
CREATE UNIQUE INDEX IF NOT EXISTS idx_machines_serial
    ON machines(serial_normalized)
    WHERE serial_normalized IS NOT NULL AND merged_into IS NULL;

CREATE INDEX IF NOT EXISTS idx_machines_brand_model
    ON machines(brand, model);

CREATE INDEX IF NOT EXISTS idx_machines_active
    ON machines(is_active)
    WHERE is_active = TRUE;


-- =============================================================================
-- 3. Listings
--    A specific dealer's advertisement for a machine on a particular source.
--    One machine may have multiple simultaneous or sequential listings.
-- =============================================================================

CREATE TABLE IF NOT EXISTS listings (
    id                       SERIAL       PRIMARY KEY,
    machine_id               INTEGER      REFERENCES machines(id),
    -- Listing identity
    source                   VARCHAR(100) NOT NULL,
    source_listing_id        VARCHAR(200),
    seller                   VARCHAR(200),
    state                    VARCHAR(5),
    -- Current state (denormalized from latest observation for fast reads)
    current_price            FLOAT,
    current_meter            FLOAT,
    current_condition        VARCHAR(50),
    current_config           TEXT,
    -- Lifecycle
    first_observed_at        TIMESTAMP    NOT NULL DEFAULT NOW(),
    last_observed_at         TIMESTAMP    NOT NULL DEFAULT NOW(),
    last_not_observed_at     TIMESTAMP,
    consecutive_valid_misses INTEGER               DEFAULT 0,
    possibly_missing         BOOLEAN               DEFAULT FALSE,
    is_active                BOOLEAN               DEFAULT TRUE,
    -- Link to current inventory record (nullable; updated each scrape)
    inventory_record_id      INTEGER      REFERENCES inventory(id),
    created_at               TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_listings_machine_id
    ON listings(machine_id);

CREATE INDEX IF NOT EXISTS idx_listings_source
    ON listings(source);

CREATE INDEX IF NOT EXISTS idx_listings_active
    ON listings(is_active)
    WHERE is_active = TRUE;

-- Composite index for listing upsert lookup (source + source_listing_id)
CREATE INDEX IF NOT EXISTS idx_listings_source_listing_id
    ON listings(source, source_listing_id)
    WHERE source_listing_id IS NOT NULL;

-- Unique constraint: one active listing per source+source_listing_id
-- Partial unique index: only for rows with a stable source_listing_id
CREATE UNIQUE INDEX IF NOT EXISTS idx_listings_unique_active
    ON listings(source, source_listing_id)
    WHERE source_listing_id IS NOT NULL AND is_active = TRUE;


-- =============================================================================
-- 4. Listing Observations
--    Written at most once per listing per calendar day (plus on first appearance
--    and on removal). Not written every scrape. Source data for analytics.
-- =============================================================================

CREATE TABLE IF NOT EXISTS listing_observations (
    id             SERIAL       PRIMARY KEY,
    listing_id     INTEGER      REFERENCES listings(id),
    machine_id     INTEGER      REFERENCES machines(id),
    -- Full observation snapshot
    source         VARCHAR(100),
    seller         VARCHAR(200),
    state          VARCHAR(5),
    price          FLOAT,
    total_meter    FLOAT,
    color_meter    FLOAT,
    bw_meter       FLOAT,
    condition      VARCHAR(50),
    feeder_model   VARCHAR(100),
    capacity       VARCHAR(200),
    finisher       VARCHAR(200),
    is_color       VARCHAR(10),
    description    TEXT,
    -- Provenance
    observed_at    TIMESTAMP    NOT NULL,
    scrape_run_id  INTEGER      REFERENCES scrape_runs(id),
    -- Change flags vs prior observation for this listing
    price_changed  BOOLEAN               DEFAULT FALSE,
    meter_changed  BOOLEAN               DEFAULT FALSE,
    seller_changed BOOLEAN               DEFAULT FALSE,
    is_first       BOOLEAN               DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_obs_listing_time
    ON listing_observations(listing_id, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_obs_machine_time
    ON listing_observations(machine_id, observed_at DESC);

-- Partial index: price history analysis (most common analytics query)
CREATE INDEX IF NOT EXISTS idx_obs_price_changes
    ON listing_observations(machine_id, observed_at)
    WHERE price_changed = TRUE;

-- Idempotency index: one observation per listing per calendar day
CREATE UNIQUE INDEX IF NOT EXISTS idx_obs_listing_daily
    ON listing_observations(listing_id, DATE(observed_at));


-- =============================================================================
-- 5. Machine Events
--    Immutable record of meaningful state transitions. Append-only.
--    Three categories: observed (scraped facts), inferred, confirmed.
-- =============================================================================

CREATE TABLE IF NOT EXISTS machine_events (
    id                SERIAL       PRIMARY KEY,
    machine_id        INTEGER      REFERENCES machines(id),
    listing_id        INTEGER      REFERENCES listings(id),
    observation_id    INTEGER      REFERENCES listing_observations(id),
    -- Classification
    event_type        VARCHAR(40)  NOT NULL,
    event_category    VARCHAR(20)  NOT NULL DEFAULT 'observed',
    -- Snapshot values at event time
    source            VARCHAR(100),
    seller            VARCHAR(200),
    state             VARCHAR(5),
    price             FLOAT,
    total_meter       FLOAT,
    condition         VARCHAR(50),
    -- Change deltas (populated for *_CHANGED events)
    prev_price        FLOAT,
    price_delta       FLOAT,
    price_delta_pct   FLOAT,
    prev_meter        FLOAT,
    meter_delta       FLOAT,
    prev_seller       VARCHAR(200),
    prev_source       VARCHAR(100),
    prev_state        VARCHAR(5),
    -- Human-readable description (rendered verbatim in UI — no interpretation added)
    description       TEXT,
    -- Provenance
    confidence        FLOAT                 DEFAULT 1.0,
    verification_type VARCHAR(20)           DEFAULT 'scraped',
    occurred_at       TIMESTAMP    NOT NULL,
    scrape_run_id     INTEGER      REFERENCES scrape_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_events_machine_time
    ON machine_events(machine_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_events_type
    ON machine_events(event_type);

-- Price drop surfacing
CREATE INDEX IF NOT EXISTS idx_events_price_drop
    ON machine_events(price_delta_pct, occurred_at)
    WHERE event_type = 'PRICE_CHANGED' AND price_delta_pct < 0;

-- Removal detection
CREATE INDEX IF NOT EXISTS idx_events_not_observed
    ON machine_events(occurred_at)
    WHERE event_type = 'LISTING_NOT_OBSERVED';

-- Idempotency: one FIRST_OBSERVED event per machine
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_first_observed_unique
    ON machine_events(machine_id)
    WHERE event_type = 'FIRST_OBSERVED' AND event_category = 'observed';

-- Idempotency: one LISTING_NOT_OBSERVED per listing per scrape run
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_not_observed_unique
    ON machine_events(listing_id, scrape_run_id)
    WHERE event_type = 'LISTING_NOT_OBSERVED';


-- =============================================================================
-- 6. Computed Values
--    Central store for all versioned analytics and predictions.
--    Every derived value records algorithm, confidence, and explanation.
-- =============================================================================

CREATE TABLE IF NOT EXISTS computed_values (
    id               SERIAL       PRIMARY KEY,
    -- What entity this value describes
    entity_type      VARCHAR(30)  NOT NULL,
    entity_id        INTEGER      NOT NULL,
    value_type       VARCHAR(50)  NOT NULL,
    value_category   VARCHAR(20)  NOT NULL,
    -- The computed value (use whichever field is appropriate for the value type)
    numeric_value    FLOAT,
    text_value       TEXT,
    json_value       TEXT,
    -- Provenance (all required)
    algorithm_id     VARCHAR(100) REFERENCES algorithm_registry(id),
    confidence       FLOAT        NOT NULL DEFAULT 1.0,
    explanation      TEXT         NOT NULL DEFAULT '',
    explanation_json TEXT,
    input_snapshot   TEXT,
    observation_ids  TEXT,
    -- Lifecycle
    computed_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
    valid_until      TIMESTAMP,
    superseded_by    INTEGER      REFERENCES computed_values(id),
    is_current       BOOLEAN               DEFAULT TRUE
);

-- Primary lookup: current value for an entity
CREATE INDEX IF NOT EXISTS idx_cv_entity
    ON computed_values(entity_type, entity_id, value_type)
    WHERE is_current = TRUE;

CREATE INDEX IF NOT EXISTS idx_cv_type_current
    ON computed_values(value_type, computed_at DESC)
    WHERE is_current = TRUE;

CREATE INDEX IF NOT EXISTS idx_cv_algorithm
    ON computed_values(algorithm_id);

-- Idempotency: one current value per entity+value_type
CREATE UNIQUE INDEX IF NOT EXISTS idx_cv_current_unique
    ON computed_values(entity_type, entity_id, value_type)
    WHERE is_current = TRUE;


-- =============================================================================
-- 7. Identity Review Queue
--    High-confidence (non-serial) possible machine matches pending human review.
--    Never auto-merged. Reviewer actions: merge, reject, uncertain, split.
-- =============================================================================

CREATE TABLE IF NOT EXISTS identity_review_queue (
    id            SERIAL      PRIMARY KEY,
    machine_id_a  INTEGER     NOT NULL REFERENCES machines(id),
    machine_id_b  INTEGER     NOT NULL REFERENCES machines(id),
    match_signals TEXT        NOT NULL DEFAULT '{}',
    confidence    FLOAT       NOT NULL,
    status        VARCHAR(20) NOT NULL DEFAULT 'pending',
    reviewed_by   VARCHAR(100),
    reviewed_at   TIMESTAMP,
    review_notes  TEXT,
    created_at    TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_review_pending
    ON identity_review_queue(status)
    WHERE status = 'pending';

-- Idempotency: one pending review per machine pair
CREATE UNIQUE INDEX IF NOT EXISTS idx_review_pair_unique
    ON identity_review_queue(
        LEAST(machine_id_a, machine_id_b),
        GREATEST(machine_id_a, machine_id_b)
    )
    WHERE status = 'pending';


-- =============================================================================
-- 8. Identity Audit
--    Immutable log of every merge and split. Preserves full correction history.
-- =============================================================================

CREATE TABLE IF NOT EXISTS identity_audit (
    id                   SERIAL      PRIMARY KEY,
    action               VARCHAR(20) NOT NULL,
    machine_id_primary   INTEGER     REFERENCES machines(id),
    machine_id_secondary INTEGER     REFERENCES machines(id),
    performed_by         VARCHAR(100),
    reason               TEXT,
    occurred_at          TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_identity_audit_primary
    ON identity_audit(machine_id_primary);


-- =============================================================================
-- 9. Buyer Activity
--    Event log for buyer/watchlist interactions. Written by scraper + mailer.
--    Uses distinct activity_type values to prevent duplicate writes:
--      MATCH_IDENTIFIED  — scraper found a qualifying match
--      NOTIFICATION_SENT — mailer confirmed email delivery
--    Both are idempotent per watchlist_id + inventory_id + scrape_run_id.
-- =============================================================================

CREATE TABLE IF NOT EXISTS buyer_activity (
    id             SERIAL      PRIMARY KEY,
    watchlist_id   VARCHAR(36) REFERENCES watchlist(id),
    activity_type  VARCHAR(30) NOT NULL,
    machine_id     INTEGER     REFERENCES machines(id),
    listing_id     INTEGER     REFERENCES listings(id),
    inventory_id   INTEGER     REFERENCES inventory(id),
    scrape_run_id  INTEGER     REFERENCES scrape_runs(id),
    note           TEXT,
    old_value      TEXT,
    new_value      TEXT,
    occurred_at    TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_buyer_activity_watchlist
    ON buyer_activity(watchlist_id);

CREATE INDEX IF NOT EXISTS idx_buyer_activity_machine
    ON buyer_activity(machine_id);

-- Idempotency: one MATCH_IDENTIFIED per watchlist+inventory record per scrape run
CREATE UNIQUE INDEX IF NOT EXISTS idx_buyer_activity_match_unique
    ON buyer_activity(watchlist_id, inventory_id, scrape_run_id)
    WHERE activity_type = 'MATCH_IDENTIFIED';

-- Idempotency: one NOTIFICATION_SENT per watchlist per scrape run
CREATE UNIQUE INDEX IF NOT EXISTS idx_buyer_activity_notif_unique
    ON buyer_activity(watchlist_id, scrape_run_id)
    WHERE activity_type = 'NOTIFICATION_SENT';


-- =============================================================================
-- New columns on: inventory
-- =============================================================================

ALTER TABLE inventory ADD COLUMN IF NOT EXISTS machine_id             INTEGER REFERENCES machines(id);
ALTER TABLE inventory ADD COLUMN IF NOT EXISTS listing_id             INTEGER REFERENCES listings(id);
ALTER TABLE inventory ADD COLUMN IF NOT EXISTS last_observed_at       TIMESTAMP;
ALTER TABLE inventory ADD COLUMN IF NOT EXISTS opportunity_score      FLOAT;
ALTER TABLE inventory ADD COLUMN IF NOT EXISTS score_reasons          TEXT;
ALTER TABLE inventory ADD COLUMN IF NOT EXISTS estimated_market_value FLOAT;
ALTER TABLE inventory ADD COLUMN IF NOT EXISTS emv_confidence         FLOAT;
ALTER TABLE inventory ADD COLUMN IF NOT EXISTS emv_algorithm_id       VARCHAR(100) REFERENCES algorithm_registry(id);
ALTER TABLE inventory ADD COLUMN IF NOT EXISTS days_on_market         INTEGER;

CREATE INDEX IF NOT EXISTS idx_inv_machine_id
    ON inventory(machine_id)
    WHERE machine_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_inv_opportunity
    ON inventory(opportunity_score DESC)
    WHERE opportunity_score IS NOT NULL;


-- =============================================================================
-- New columns on: watchlist
-- =============================================================================

ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS budget_min        FLOAT;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS urgency           VARCHAR(20);
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS financing         VARCHAR(20);
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS preferred_states  TEXT;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS alt_brands        TEXT;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS alt_models        TEXT;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS status            VARCHAR(20) DEFAULT 'active';
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS match_count_total INTEGER     DEFAULT 0;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS last_match_at     TIMESTAMP;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS last_notified_at  TIMESTAMP;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS ai_profile        TEXT;
