# scraper.py — fetchers → normalize → DB upsert

import glob
import hashlib
import os
import traceback
from datetime import datetime

import pandas as pd

import normalizer
from aggregator import _find_matches, _safe_num
from config import MANUAL_SOURCES, OUTPUT_COLUMNS, SOURCES
from db import InventoryRecord, ScrapeRun, WatchlistItem

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
    if str(rec.get("print_speed", "")).upper() in ("YES", "Y"):
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

def _fetch_source(key: str) -> list[pd.DataFrame]:
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
            return []

        raw = fetch()
        if raw is None or raw.empty:
            print(f"    No data returned.")
            return []

        df = normalizer.normalize(raw, name)
        print(f"    {len(df)} rows fetched.")
        return [df]
    except Exception as exc:
        print(f"    ERROR fetching {key}: {exc}")
        traceback.print_exc()
        return []


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


def _load_imports_from_volume(upload_dir: str) -> list[pd.DataFrame]:
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
                    sheet_source = _source_from_sheet(sheet_name)
                    raw["_raw_source"] = sheet_source
                    df = normalizer.normalize(raw, sheet_source)
                    if not df.empty:
                        frames.append(df)
                        print(f"  [import] {sheet_source}: {len(df)} rows from {basename} [{sheet_name}]")
        except Exception as exc:
            print(f"  [import] ERROR loading {filepath}: {exc}")

    return frames


# ---------------------------------------------------------------------------
# DB upsert
# ---------------------------------------------------------------------------

def _find_existing(db, row: dict) -> InventoryRecord | None:
    serial = _str_or_none(row.get("serial"))
    inv    = _str_or_none(row.get("inv"))
    if serial:
        rec = db.query(InventoryRecord).filter(InventoryRecord.serial == serial).first()
        if rec:
            return rec
    if inv:
        rec = db.query(InventoryRecord).filter(InventoryRecord.inv == inv).first()
        if rec:
            return rec
    return None


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
    # Build config
    cfg_dict = {
        "feeder_model": record.feeder_model or "",
        "capacity":     record.capacity or "",
        "finisher":     record.finisher or "",
        "print_speed":  record.print_speed or "",
        "scan":         record.scan or "",
        "fax":          record.fax or "",
    }
    record.config = _build_config(cfg_dict)


def _upsert_inventory(db, master: pd.DataFrame, run: ScrapeRun) -> int:
    now = datetime.utcnow()
    new_count = 0
    for _, pandas_row in master.iterrows():
        row = _row_to_dict(pandas_row)
        existing = _find_existing(db, row)
        if existing:
            _apply_fields(existing, row, run.id, now)
        else:
            rec = InventoryRecord(first_seen_at=now, is_new=True)
            _apply_fields(rec, row, run.id, now)
            db.add(rec)
            new_count += 1
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

        if not imports_only:
            keys = sources or list(SOURCES.keys())
            for key in keys:
                frames.extend(_fetch_source(key))

        frames.extend(_load_imports_from_volume(UPLOAD_DIR))

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

        new_count = _upsert_inventory(db, master, run)

        run.status = "success"
        run.total_records = len(master)
        run.new_records = new_count
        run.finished_at = datetime.utcnow()
        db.commit()

        print(f"[scraper] Run #{run.id} done — {len(master)} records, {new_count} new.")

        _check_and_notify(db, run)

    except Exception as exc:
        traceback.print_exc()
        run.status = "error"
        run.error = str(exc)
        run.finished_at = datetime.utcnow()
        db.commit()

    return run
