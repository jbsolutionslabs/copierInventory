# scraper.py — fetchers → normalize → DB upsert

import glob
import hashlib
import os
import traceback
from datetime import datetime

import pandas as pd

import normalizer
from aggregator import _find_matches, _safe_num
from config import MANUAL_SOURCES, OUTPUT_COLUMNS, SOURCES, get_listing_id_field
from db import InventoryRecord, Listing, Machine, ScrapeRun, WatchlistItem

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "imports"))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_config(rec: dict) -> str:
    parts = []
    if rec.get("feeder_model"):
        parts.append(rec["feeder_model"])
    if rec.get("capacity"):
        parts.append(rec["capacity"])
    if rec.get("finisher"):
        parts.append(rec["finisher"])
    ps = str(rec.get("print_speed", "")).strip().upper()
    if ps and ps not in ("NO", "N", "FALSE", "0", "NAN", "NONE"):
        parts.append("Print")
    if str(rec.get("scan", "")).upper() in ("YES", "Y"):
        parts.append("Scan")
    if str(rec.get("fax", "")).upper() in ("YES", "Y"):
        parts.append("Fax")
    return " | ".join(parts)


def _row_to_dict(row: pd.Series) -> dict:
    d = {}
    for col in OUTPUT_COLUMNS:
        val = row.get(col)
        if pd.isna(val) if isinstance(val, float) else val is None:
            d[col] = None
        else:
            d[col] = val
    return d


def _float_or_none(val) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def _str_or_none(val) -> str | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    return s if s and s.lower() not in ("nan", "none", "") else None


# ---------------------------------------------------------------------------
# Source fetching (mirrors run.py logic)
# ---------------------------------------------------------------------------

def _fetch_source(key: str) -> tuple[list[pd.DataFrame], bool]:
    """Fetch and normalize one source. Returns (frames, success) where success=True
    means no exception was raised (empty response is still success=True)."""
    cfg = SOURCES[key]
    name = cfg["name"]
    print(f"  [scraper] Fetching {key.upper()} — {name}")
    try:
        if key == "rci":
            from fetchers.rci import fetch
        elif key == "als":
            from fetchers.als import fetch
        elif key == "ars":
            from fetchers.equipment_recovery import fetch
        elif key == "copex":
            from fetchers.copex import fetch
        elif key == "rsi":
            from fetchers.rsi import fetch
        elif key == "tnt":
            from fetchers.tnt import fetch
        else:
            print(f"    No fetcher for '{key}' — skipping.")
            return [], False

        raw = fetch()
        if raw is None or raw.empty:
            print(f"    No data returned.")
            return [], True  # fetch succeeded, source just had no data

        df = normalizer.normalize(raw, name)
        print(f"    {len(df)} rows fetched.")
        return [df], True
    except Exception as exc:
        print(f"    ERROR fetching {key}: {exc}")
        traceback.print_exc()
        return [], False


# ---------------------------------------------------------------------------
# Manual import loader (from Railway Volume / local imports dir)
# ---------------------------------------------------------------------------

def _source_name_from_filename(filename: str) -> str:
    base = os.path.splitext(os.path.basename(filename))[0].lower()
    for key, display_name in MANUAL_SOURCES.items():
        if key in base:
            return display_name
    return base.replace("_", " ").replace("-", " ").title()


def _source_from_sheet(sheet_name: str) -> str:
    lower = sheet_name.strip().lower()
    for key, display_name in MANUAL_SOURCES.items():
        if key in lower:
            return display_name
    return sheet_name.strip()


def _file_hash(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def _load_imports_from_volume(upload_dir: str, source_lookup: dict | None = None) -> list[pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    if not os.path.isdir(upload_dir):
        return frames

    patterns = ["*.xlsx", "*.xls", "*.csv"]
    files: list[str] = []
    for pat in patterns:
        files.extend(glob.glob(os.path.join(upload_dir, pat)))

    seen_hashes: dict[str, str] = {}
    for filepath in sorted(files):
        basename = os.path.basename(filepath)
        digest = _file_hash(filepath)
        if digest in seen_hashes:
            print(f"  [import] SKIPPED {basename} — duplicate of '{seen_hashes[digest]}'")
            continue
        seen_hashes[digest] = basename

        # Prefer DB-sourced name (handles custom sources); fall back to filename detection
        if source_lookup and basename in source_lookup:
            source_name = source_lookup[basename]
        else:
            source_name = _source_name_from_filename(filepath)
        try:
            if filepath.endswith(".csv"):
                raw = pd.read_csv(filepath, dtype=str)
                raw["_raw_source"] = source_name
                df = normalizer.normalize(raw, source_name)
                if not df.empty:
                    frames.append(df)
                    print(f"  [import] {source_name}: {len(df)} rows from {basename}")
            else:
                sheets = pd.read_excel(filepath, sheet_name=None, dtype=str)
                for sheet_name, raw in sheets.items():
                    if raw.empty:
                        continue
                    # If we have a user-specified source (from DB), use it for every sheet.
                    # Only fall back to sheet name detection when no DB source is known.
                    sheet_source = source_name if source_name else _source_from_sheet(sheet_name)
                    raw["_raw_source"] = sheet_source
                    df = normalizer.normalize(raw, sheet_source)
                    if not df.empty:
                        frames.append(df)
                        print(f"  [import] {sheet_source}: {len(df)} rows from {basename} [{sheet_name}]")
        except Exception as exc:
            print(f"  [import] ERROR loading {filepath}: {exc}")

    return frames


# ---------------------------------------------------------------------------
# Phase 3 — Machine identity resolution + Listing upsert
# ---------------------------------------------------------------------------

def _link_machine_and_listing(
    db,
    rec: InventoryRecord,
    row: dict,
    run_id: int,
    now: datetime,
) -> None:
    """
    Resolve machine identity for a scraped row and upsert its Listing record.
    Writes machine_id and listing_id back to rec (caller must flush/commit).

    Called inside _replace_by_source after each InventoryRecord is flushed
    (so rec.id is already populated). Errors are logged but never crash the
    scrape — identity linkage is best-effort in Phase 3.
    """
    from identity import listing_fingerprint, resolve_machine_identity

    try:
        # 1. Resolve physical machine identity
        resolution = resolve_machine_identity(db, row)
        rec.machine_id = resolution.machine_id

        # 2. Touch machine.last_observed_at
        machine = db.get(Machine, resolution.machine_id)
        if machine:
            machine.last_observed_at = now

        # 3. Determine stable listing identifier
        source_name = _str_or_none(row.get("source")) or ""
        id_field = get_listing_id_field(source_name)
        source_listing_id = _str_or_none(row.get(id_field))
        # Fall back to fingerprint when no stable ID (inv/serial) is present
        if not source_listing_id:
            source_listing_id = listing_fingerprint(row)

        # 4. Find or create the Listing for (source, source_listing_id)
        existing = (
            db.query(Listing)
            .filter(
                Listing.source == source_name,
                Listing.source_listing_id == source_listing_id,
            )
            .order_by(Listing.created_at.desc())
            .first()
        )

        config_str = _build_config(row)

        if existing:
            # Update mutable fields; reactivate if it had gone inactive
            existing.machine_id               = resolution.machine_id
            existing.last_observed_at         = now
            existing.current_price            = _float_or_none(row.get("price"))
            existing.current_meter            = _float_or_none(row.get("total_meter"))
            existing.current_condition        = _str_or_none(row.get("condition"))
            existing.current_config           = config_str
            existing.seller                   = source_name
            existing.state                    = _str_or_none(row.get("state"))
            existing.is_active                = True
            existing.consecutive_valid_misses = 0
            existing.possibly_missing         = False
            existing.inventory_record_id      = rec.id
            listing = existing
        else:
            sp = db.begin_nested()
            listing = Listing(
                machine_id          = resolution.machine_id,
                source              = source_name,
                source_listing_id   = source_listing_id,
                seller              = source_name,
                state               = _str_or_none(row.get("state")),
                current_price       = _float_or_none(row.get("price")),
                current_meter       = _float_or_none(row.get("total_meter")),
                current_condition   = _str_or_none(row.get("condition")),
                current_config      = config_str,
                first_observed_at   = now,
                last_observed_at    = now,
                is_active           = True,
                inventory_record_id = rec.id,
            )
            db.add(listing)
            sp.commit()
            db.flush()

        rec.listing_id = listing.id

    except Exception as exc:
        print(f"  [identity] WARNING: could not link row to machine/listing: {exc}")


# ---------------------------------------------------------------------------
# DB replace-by-source
# ---------------------------------------------------------------------------

def _apply_fields(record: InventoryRecord, row: dict, run_id: int, now: datetime):
    record.source       = _str_or_none(row.get("source"))
    record.brand        = _str_or_none(row.get("brand"))
    record.model        = _str_or_none(row.get("model"))
    record.condition    = _str_or_none(row.get("condition"))
    record.state        = _str_or_none(row.get("state"))
    record.inv          = _str_or_none(row.get("inv"))
    record.serial       = _str_or_none(row.get("serial"))
    record.total_meter  = _float_or_none(row.get("total_meter"))
    record.color_meter  = _float_or_none(row.get("color_meter"))
    record.bw_meter     = _float_or_none(row.get("bw_meter"))
    record.is_color     = _str_or_none(row.get("is_color"))
    record.feeder_model = _str_or_none(row.get("feeder_model"))
    record.capacity     = _str_or_none(row.get("capacity"))
    record.finisher     = _str_or_none(row.get("finisher"))
    record.print_speed  = _str_or_none(row.get("print_speed"))
    record.scan         = _str_or_none(row.get("scan"))
    record.fax          = _str_or_none(row.get("fax"))
    record.qty          = _float_or_none(row.get("qty"))
    record.price        = _float_or_none(row.get("price"))
    record.description  = _str_or_none(row.get("description"))
    record.notes        = _str_or_none(row.get("notes"))
    record.last_seen_at = now
    record.scrape_run_id = run_id
    cfg_dict = {
        "feeder_model": record.feeder_model or "",
        "capacity":     record.capacity or "",
        "finisher":     record.finisher or "",
        "print_speed":  record.print_speed or "",
        "scan":         record.scan or "",
        "fax":          record.fax or "",
    }
    record.config = _build_config(cfg_dict)


def _replace_by_source(db, master: pd.DataFrame, run: ScrapeRun) -> int:
    """
    For each source in master:
      1. Snapshot existing serial→first_seen_at and inv→first_seen_at BEFORE deleting.
      2. Delete ALL existing records for that source.
      3. Re-insert fresh rows, restoring first_seen_at for previously known items.

    isNew = True only for records whose serial/inv was never seen before.
    This guarantees zero duplicates regardless of serial/inv availability.
    """
    now = datetime.utcnow()

    # --- Step 1: snapshot all known identifiers across ALL sources ---
    existing = db.query(
        InventoryRecord.serial,
        InventoryRecord.inv,
        InventoryRecord.first_seen_at,
    ).all()

    # serial → earliest first_seen_at (in case of pre-existing dupes)
    serial_seen: dict[str, datetime] = {}
    inv_seen:    dict[str, datetime] = {}
    for r in existing:
        if r.serial:
            if r.serial not in serial_seen or r.first_seen_at < serial_seen[r.serial]:
                serial_seen[r.serial] = r.first_seen_at
        if r.inv:
            if r.inv not in inv_seen or r.first_seen_at < inv_seen[r.inv]:
                inv_seen[r.inv] = r.first_seen_at

    # --- Step 2: delete all rows for every source we're about to replace ---
    sources_in_run = [s for s in master["source"].dropna().unique() if s]
    for source in sources_in_run:
        deleted = db.query(InventoryRecord).filter(InventoryRecord.source == source).delete()
        print(f"  [db] Cleared {deleted} old record(s) for source '{source}'")
    db.flush()

    # --- Step 3: insert fresh rows ---
    new_count = 0
    for _, pandas_row in master.iterrows():
        row = _row_to_dict(pandas_row)
        serial = _str_or_none(row.get("serial"))
        inv    = _str_or_none(row.get("inv"))

        # Restore first_seen_at if this item was seen before; else it's genuinely new
        if serial and serial in serial_seen:
            first_seen = serial_seen[serial]
            is_new = False
        elif inv and inv in inv_seen:
            first_seen = inv_seen[inv]
            is_new = False
        else:
            first_seen = now
            is_new = True
            new_count += 1

        rec = InventoryRecord(first_seen_at=first_seen, is_new=is_new)
        _apply_fields(rec, row, run.id, now)
        db.add(rec)
        db.flush()  # populate rec.id before Phase 3 linking

        _link_machine_and_listing(db, rec, row, run.id, now)  # Phase 3

    db.commit()
    return new_count


# ---------------------------------------------------------------------------
# Dedup (same logic as aggregator.write_excel)
# ---------------------------------------------------------------------------

def _dedup(master: pd.DataFrame) -> pd.DataFrame:
    def _score_df(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["_score"] = (
            (pd.to_numeric(df.get("price",       pd.Series(dtype=float)), errors="coerce").fillna(0) > 0).astype(int) * 4 +
            (pd.to_numeric(df.get("total_meter", pd.Series(dtype=float)), errors="coerce").fillna(0) > 0).astype(int) * 2 +
            (df.get("description", pd.Series(dtype=str)).fillna("").str.len() > 0).astype(int)
        )
        return df

    has_serial = (
        master["serial"].notna() & (master["serial"].astype(str).str.strip() != "")
        if "serial" in master.columns
        else pd.Series(False, index=master.index)
    )
    has_inv = (
        master["inv"].notna() & (master["inv"].astype(str).str.strip() != "")
        if "inv" in master.columns
        else pd.Series(False, index=master.index)
    )

    grp_serial = master[has_serial].copy()
    grp_inv    = master[~has_serial & has_inv].copy()
    grp_none   = master[~has_serial & ~has_inv].copy()

    if not grp_serial.empty:
        grp_serial = _score_df(grp_serial).sort_values("_score", ascending=False)
        grp_serial = grp_serial.drop_duplicates(subset=["serial"], keep="first").drop(columns=["_score"])

    if not grp_inv.empty:
        grp_inv = _score_df(grp_inv).sort_values("_score", ascending=False)
        grp_inv = grp_inv.drop_duplicates(subset=["inv"], keep="first").drop(columns=["_score"])

    return pd.concat([grp_serial, grp_inv, grp_none], ignore_index=True)


# ---------------------------------------------------------------------------
# Watchlist matching + email notification
# ---------------------------------------------------------------------------

def _record_to_json_dict(rec: InventoryRecord, run_started_at: datetime) -> dict:
    """Convert an ORM record to the same dict shape the frontend expects."""
    def _clean_num(v):
        if v is None:
            return 0
        try:
            f = float(v)
            return int(f) if f == int(f) else f
        except (TypeError, ValueError):
            return 0

    return {
        "vendor":      rec.source or "",
        "brand":       rec.brand or "",
        "model":       rec.model or "",
        "condition":   rec.condition or "",
        "state":       rec.state or "",
        "inv":         rec.inv or "",
        "serial":      rec.serial or "",
        "total":       _clean_num(rec.total_meter),
        "color":       _clean_num(rec.color_meter),
        "bw":          _clean_num(rec.bw_meter),
        "isColor":     rec.is_color or "",
        "feederModel": rec.feeder_model or "",
        "capacity":    rec.capacity or "",
        "finisher":    rec.finisher or "",
        "print":       rec.print_speed or "",
        "scan":        rec.scan or "",
        "fax":         rec.fax or "",
        "qty":         _clean_num(rec.qty),
        "price":       _clean_num(rec.price),
        "description": rec.description or "",
        "notes":       rec.notes or "",
        "isNew":       rec.first_seen_at is not None and rec.first_seen_at >= run_started_at,
        "config":      rec.config or "",
    }


def _check_and_notify(db, run: ScrapeRun):
    from mailer import send_watchlist_match

    watchlist: list[WatchlistItem] = db.query(WatchlistItem).all()
    if not watchlist:
        return

    # Fetch records marked new in this run
    new_records = db.query(InventoryRecord).filter(
        InventoryRecord.scrape_run_id == run.id,
        InventoryRecord.first_seen_at >= run.started_at,
    ).all()

    if not new_records:
        return

    new_json = [_record_to_json_dict(r, run.started_at) for r in new_records]

    for item in watchlist:
        if not item.email:
            continue
        req = {
            "brand":    item.brand or "",
            "model":    item.model or "",
            "color":    item.color or "",
            "state":    item.state or "",
            "finisher": item.finisher or "",
            "fax":      item.fax or "",
            "maxMeter": item.max_meter,
            "maxPrice": item.max_price,
        }
        matches = _find_matches(req, new_json)
        if matches:
            try:
                send_watchlist_match(
                    customer_name=item.name or item.email,
                    email=item.email,
                    matches=matches[:20],
                    criteria=req,
                )
                print(f"  [notify] Sent {len(matches)} match(es) to {item.email}")
            except Exception as exc:
                print(f"  [notify] Email failed for {item.email}: {exc}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_scrape(db, sources: list[str] | None = None, imports_only: bool = False):
    """
    Run a full scrape cycle: fetch → normalize → dedup → upsert → notify.
    """
    now = datetime.utcnow()
    run = ScrapeRun(started_at=now, status="running")
    db.add(run)
    db.commit()

    print(f"[scraper] Run #{run.id} started at {now.isoformat()}")

    try:
        frames: list[pd.DataFrame] = []
        # source_stats tracks per-source success/count for the history engine
        source_stats: dict[str, dict] = {}

        if not imports_only:
            keys = sources or list(SOURCES.keys())
            for key in keys:
                source_frames, fetch_ok = _fetch_source(key)
                frames.extend(source_frames)
                source_name = SOURCES[key]["name"]
                source_stats[source_name] = {
                    "success": fetch_ok,
                    "record_count": sum(len(f) for f in source_frames),
                }

        # Build filename→display_name lookup from DB so custom sources are resolved correctly
        from db import UploadedFile as _UF
        source_lookup = {
            uf.filename: (
                MANUAL_SOURCES.get(uf.source_key, uf.source_key.replace("_", " ").title())
                if uf.source_key else None
            )
            for uf in db.query(_UF).all()
            if uf.filename and uf.source_key
        }
        frames.extend(_load_imports_from_volume(UPLOAD_DIR, source_lookup=source_lookup))

        if not frames:
            print("[scraper] No data collected.")
            run.status = "success"
            run.total_records = 0
            run.new_records = 0
            run.finished_at = datetime.utcnow()
            db.commit()
            return run

        master = pd.concat(frames, ignore_index=True)
        master = _dedup(master)

        # Fill in stats for manual import sources (not captured in _fetch_source loop)
        for sn in master["source"].dropna().unique():
            sn = str(sn)
            if sn not in source_stats:
                count = int((master["source"] == sn).sum())
                source_stats[sn] = {"success": True, "record_count": count}

        new_count = _replace_by_source(db, master, run)

        run.status = "success"
        run.total_records = len(master)
        run.new_records = new_count
        run.finished_at = datetime.utcnow()
        db.commit()

        print(f"[scraper] Run #{run.id} done — {len(master)} records, {new_count} new.")

        # Phase 4: history engine (observations + events + miss counters)
        from history import run_history_engine
        run_history_engine(db, run, source_stats)
        db.commit()

        _check_and_notify(db, run)

    except Exception as exc:
        traceback.print_exc()
        run.status = "error"
        run.error = str(exc)
        run.finished_at = datetime.utcnow()
        db.commit()

    return run
