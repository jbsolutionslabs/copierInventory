# aggregator.py — merges all normalized DataFrames and writes Excel + JSON output

import os
import glob
import json
import hashlib
import datetime
import pandas as pd
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

import normalizer
from config import OUTPUT_COLUMNS, MANUAL_SOURCES


OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
IMPORTS_DIR = os.path.join(os.path.dirname(__file__), "imports")
DOCS_DATA_DIR = os.path.join(os.path.dirname(__file__), "docs", "data")


# ---------------------------------------------------------------------------
# Manual import loader
# ---------------------------------------------------------------------------

def _source_name_from_filename(filename: str) -> str:
    """
    Infer source name from a filename like:
      tnt_inventory_2024-05-21.xlsx  →  "TNT Copiers"
      mars.csv                       →  "Mars"
    """
    base = os.path.splitext(os.path.basename(filename))[0].lower()
    for key, display_name in MANUAL_SOURCES.items():
        if key in base:
            return display_name
    # Fallback: title-case the base name (strip underscores/dashes)
    return base.replace("_", " ").replace("-", " ").title()


def _file_hash(filepath: str) -> str:
    """Return SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def load_manual_imports() -> list[pd.DataFrame]:
    """Load all CSV/Excel files from the imports/ directory, skipping duplicates."""
    frames = []
    patterns = ["*.xlsx", "*.xls", "*.csv"]
    files = []
    for pat in patterns:
        files.extend(glob.glob(os.path.join(IMPORTS_DIR, pat)))

    seen_hashes: dict[str, str] = {}  # hash → first filename that had it

    for filepath in sorted(files):
        basename = os.path.basename(filepath)

        # Skip files with identical content (duplicate uploads)
        digest = _file_hash(filepath)
        if digest in seen_hashes:
            print(f"  [import] SKIPPED {basename} — duplicate of '{seen_hashes[digest]}'")
            continue
        seen_hashes[digest] = basename

        source_name = _source_name_from_filename(filepath)
        try:
            if filepath.endswith(".csv"):
                raw = pd.read_csv(filepath, dtype=str)
            else:
                raw = pd.read_excel(filepath, dtype=str)
            raw["_raw_source"] = source_name
            df = normalizer.normalize(raw, source_name)
            if not df.empty:
                frames.append(df)
                print(f"  [import] {source_name}: {len(df)} rows from {basename}")
        except Exception as exc:
            print(f"  [import] ERROR loading {filepath}: {exc}")

    return frames


# ---------------------------------------------------------------------------
# Excel styling helpers
# ---------------------------------------------------------------------------

_HEADER_FILL   = PatternFill("solid", fgColor="1F4E79")
_ALT_ROW_FILL  = PatternFill("solid", fgColor="D6E4F0")
_HEADER_FONT   = Font(bold=True, color="FFFFFF", size=11)
_TITLE_FONT    = Font(bold=True, size=13, color="1F4E79")


def _style_sheet(ws, freeze_row: int = 1):
    """Apply header formatting and auto-width to a worksheet."""
    # Style header row
    for cell in ws[freeze_row]:
        cell.fill   = _HEADER_FILL
        cell.font   = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Alternate row shading
    for i, row in enumerate(ws.iter_rows(min_row=freeze_row + 1), start=1):
        if i % 2 == 0:
            for cell in row:
                cell.fill = _ALT_ROW_FILL

    # Auto-fit column widths (cap at 50)
    for col_idx, col_cells in enumerate(ws.columns, start=1):
        max_len = max((len(str(c.value or "")) for c in col_cells), default=10)
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 4, 50)

    ws.freeze_panes = ws.cell(row=freeze_row + 1, column=1)


# ---------------------------------------------------------------------------
# Main aggregation + write
# ---------------------------------------------------------------------------

def write_excel(frames: list[pd.DataFrame]) -> str:
    """
    Combine all DataFrames, write an Excel file with:
      - A "Master" sheet (all sources combined)
      - One sheet per source
    Returns the output file path.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = datetime.date.today().strftime("%Y-%m-%d")
    out_path = os.path.join(OUTPUT_DIR, f"inventory_{today}.xlsx")

    if not frames:
        print("  [aggregator] No data to write.")
        return ""

    master = pd.concat(frames, ignore_index=True)

    # Dedup by serial number only — same serial across multiple sources = same physical machine
    if "serial" in master.columns:
        has_serial = master["serial"].notna() & (master["serial"].astype(str).str.strip() != "")
        with_serial    = master[has_serial].copy()
        without_serial = master[~has_serial].copy()

        if not with_serial.empty:
            # When the same serial appears in multiple sources, keep the record with
            # the most data (price > meter > description)
            with_serial["_score"] = (
                (pd.to_numeric(with_serial["price"], errors="coerce").fillna(0) > 0).astype(int) * 4 +
                (pd.to_numeric(with_serial["meter"], errors="coerce").fillna(0) > 0).astype(int) * 2 +
                (with_serial["description"].fillna("").str.len() > 0).astype(int)
            )
            with_serial = with_serial.sort_values("_score", ascending=False)
            with_serial = with_serial.drop_duplicates(subset=["serial"], keep="first")
            with_serial = with_serial.drop(columns=["_score"])

        master = pd.concat([with_serial, without_serial], ignore_index=True)

    # Sort for readability
    master = master.sort_values(["source", "brand", "model"]).reset_index(drop=True)

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        # Master sheet
        master.to_excel(writer, sheet_name="Master", index=False)
        _style_sheet(writer.sheets["Master"])

        # Per-source sheets
        for source in master["source"].unique():
            sub = master[master["source"] == source].reset_index(drop=True)
            sheet_name = source[:31]  # Excel sheet name limit
            sub.to_excel(writer, sheet_name=sheet_name, index=False)
            _style_sheet(writer.sheets[sheet_name])

    print(f"\n  Output written to: {out_path}")
    print(f"  Total rows: {len(master)}")

    # Also write JSON for the web UI
    _write_json(master)

    return out_path


def _write_json(master: pd.DataFrame):
    """Write docs/data/inventory.json for the GitHub Pages UI."""
    os.makedirs(DOCS_DATA_DIR, exist_ok=True)
    json_path = os.path.join(DOCS_DATA_DIR, "inventory.json")

    # Replace NaN/NaT with empty string for JSON serialization
    clean = master.where(master.notna(), other="")
    # Ensure numeric columns are plain numbers, not numpy types
    for col in ["qty", "price", "meter"]:
        if col in clean.columns:
            clean[col] = pd.to_numeric(clean[col], errors="coerce").fillna(0)

    records = clean.to_dict(orient="records")

    payload = {
        "updated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": len(records),
        "sources": sorted(master["source"].unique().tolist()),
        "brands":  sorted(master["brand"].dropna().unique().tolist()),
        "records": records,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    print(f"  JSON written to:   {json_path}")
