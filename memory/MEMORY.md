# Copier Inventory Aggregator — Project Memory

## Project
`/Users/josephbongar/Desktop/copierInventory` — tool to aggregate copier inventory from wholesaler websites.

## Architecture
### Core (unchanged)
- `run.py` — CLI entry point (`python3 run.py`)
- `config.py` — source URLs, brand alias map, OUTPUT_COLUMNS
- `fetchers/` — source-specific fetchers (rci, als, equipment_recovery, copex, rsi, tnt)
- `normalizer.py` — column + brand name standardization
- `aggregator.py` — Excel output + JSON + watchlist matching helpers
- `imports/` — drop manual files here; also used as UPLOAD_DIR for Railway Volume

### FastAPI Backend (Railway — production)
- `main.py` — FastAPI app, CORS, lifespan (init_db + scheduler)
- `db.py` — SQLAlchemy models (ScrapeRun, InventoryRecord, WatchlistItem, UploadedFile) + get_db()
- `scraper.py` — run_scrape(): fetch → normalize → dedup → upsert → notify
- `mailer.py` — Resend email for watchlist matches
- `scheduler.py` — APScheduler hourly job calling run_scrape()
- `railway.toml` — build + deploy config; Volume mounted at /data
- `requirements.txt` — all Python deps

### API Routes (Railway)
- `routes/inventory.py` — GET /api/inventory (same JSON shape as old inventory.json)
- `routes/uploads.py` — GET/POST /api/uploads, DELETE /api/uploads/{filename}
- `routes/watchlist.py` — GET/POST/PUT/DELETE /api/watchlist, POST /api/watchlist/bulk, GET /api/watchlist/matches
- `routes/scrape.py` — POST /api/scrape/trigger, GET /api/scrape/status

### Frontend (GitHub Pages)
- `docs/index.html` — web UI; `API_BASE = 'https://YOUR-APP.up.railway.app'` at top of script
- Uploads use multipart FormData to POST /api/uploads
- Watchlist synced via POST /api/watchlist/bulk (whole array); loaded from GET /api/watchlist on init

### Railway Env Vars
DATABASE_URL (auto), UPLOAD_DIR=/data/uploads, RESEND_API_KEY, EMAIL_FROM, ALLOWED_ORIGINS

## Data Schema (OUTPUT_COLUMNS → JSON field)
source→vendor, brand, model, condition, state, inv, serial, total_meter→total, color_meter→color, bw_meter→bw, is_color→isColor, feeder_model→feederModel, capacity, finisher, print_speed→print, scan, fax, qty, price, description, notes; plus computed: isNew, config (pipe-separated summary)

## Source Details
| Source | Method | Key Detail |
|---|---|---|
| RCI | HTML scrape via curl | `http://inv.rciwholesale.com/` (HTTP only — HTTPS resets) |
| ALS | WP AJAX | `action=wp_ajax_ninja_tables_public_action`, table_id=2992 |
| ARS | REST API | `https://app.equipmentrecovery.com/api/upload/getProduct?location=CA/NJ/WA` |
| TNT | Manual import | Salesforce-based, no public API |
| Wulff | Manual import | Dealer login required |
| Mars/RSI | Manual import | Send weekly/daily Excel by email |

## Usage
```bash
python3 run.py                    # fetch all + load imports/
python3 run.py --source rci als   # specific sources only
python3 run.py --imports-only     # only process manual imports
python3 run.py --list-sources     # show sources
```

## Market Intelligence Platform Plan
Full ADR at `docs/MARKET_INTELLIGENCE_ADR.md`. Key decisions:

### Core Philosophy
- Goal: "Decision Engine, not Database" — every feature answers a decision question
- Three information categories: Facts (immutable observed), Analytics (deterministic, versioned), Predictions (forward-looking, versioned)

### Key New Concepts
- **Observed Market History**: replaces "Machine Lifecycle" — only claims what was observed, never infers sales/ownership
- **Four-layer data model**: Physical Machine → Listing → Observation → Event
- **Versioned algorithms**: every computed value stores algorithm_id, version, confidence, explanation
- **Conservative identity**: serial = auto-link; multi-attribute = review queue (never auto-merge); insufficient = new identity
- **outcome_feedback table**: closes loop between predictions and confirmed outcomes for future model improvement

### New Tables (Phase 0+)
machines, listings, listing_observations, machine_events, computed_values, algorithm_registry,
machine_analytics, comparables, market_snapshots, outcome_feedback,
identity_review_queue, identity_audit, buyer_activity

### Phased Roadmap
- Phase 0 (Wk 1-2): Schema + identity resolution + event emission
- Phase 1 (Wk 3-4): Analytics engine + market_snapshots + comparables
- Phase 2 (Wk 5): Opportunity scoring (versioned, explainable)
- Phase 3 (Wk 6-7): Buyer intelligence + feedback collection
- Phase 4 (Wk 7-8): Identity review UI
- Phase 5 (Wk 9-11): Market dashboard (new tab in index.html)
- Phase 6 (Wk 12+): Forecasting, AI profiles, dealer integrations

### Critical Rules
- LISTING_NOT_OBSERVED only after 2 consecutive missed scrapes (avoid false positives from source downtime)
- All new endpoints under /api/v2/ — existing endpoints unchanged
- History layer wrapped in try/except — never blocks existing scrape pipeline
- UI must never present inferred events with same treatment as observed events
