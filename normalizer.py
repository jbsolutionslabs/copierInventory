# normalizer.py — brand name resolution and column standardization

import re
import pandas as pd
from config import BRAND_ALIASES, OUTPUT_COLUMNS, VENDOR_ALIASES

# Pre-compile a single regex that matches any alias token
_ALIAS_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(BRAND_ALIASES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def resolve_brand(raw: str) -> str:
    """
    Given a raw brand/model string, return the canonical brand name.

    Strategy:
    1. Exact (case-insensitive) lookup in BRAND_ALIASES.
    2. Strip parenthetical suffixes ("Ricoh (USA)" → "Ricoh") and retry.
    3. Token match via regex (longest alias wins, handles word boundaries).
    4. Normalize slashes/hyphens to spaces and retry exact + token match.
    5. Return title-cased original if nothing matched.
    """
    if not raw or not isinstance(raw, str):
        return ""

    clean = raw.strip()
    lower = clean.lower()

    # 1. Exact match
    if lower in BRAND_ALIASES:
        return BRAND_ALIASES[lower]

    # 2. Strip trailing parenthetical (e.g., "Ricoh (USA)", "Canon Inc.")
    stripped = re.sub(r"\s*\([^)]*\)\s*$", "", clean).strip()
    stripped = re.sub(r"\s+(inc\.?|corp\.?|ltd\.?|llc\.?|co\.?)$", "", stripped, flags=re.IGNORECASE).strip()
    lower_stripped = stripped.lower()
    if lower_stripped != lower and lower_stripped in BRAND_ALIASES:
        return BRAND_ALIASES[lower_stripped]

    # 3. Token / substring match (longest alias wins)
    match = _ALIAS_PATTERN.search(lower)
    if match:
        return BRAND_ALIASES[match.group(1).lower()]

    # 4. Normalize slashes and hyphens to spaces, retry
    normalized = re.sub(r"[/\-]", " ", lower_stripped).strip()
    if normalized in BRAND_ALIASES:
        return BRAND_ALIASES[normalized]
    match2 = _ALIAS_PATTERN.search(normalized)
    if match2:
        return BRAND_ALIASES[match2.group(1).lower()]

    # 5. Return title-cased original
    return stripped.title() if stripped else clean.title()


# Regex for trailing US state code suffix on vendor names (e.g., "ARS-NJ", "ARS-WA")
_VENDOR_LOCATION_RE = re.compile(r"-[A-Z]{2}$", re.IGNORECASE)


def _normalize_vendor_name(name: str) -> str:
    """Map raw vendor/source strings to canonical display names via VENDOR_ALIASES."""
    if not name or not isinstance(name, str):
        return name
    s = name.strip()
    lower = s.lower()
    # Direct lookup
    if lower in VENDOR_ALIASES:
        return VENDOR_ALIASES[lower]
    # Try stripping trailing state-code suffix: "ARS-NJ" → "ARS"
    without_loc = _VENDOR_LOCATION_RE.sub("", s).strip()
    if without_loc.lower() in VENDOR_ALIASES:
        return VENDOR_ALIASES[without_loc.lower()]
    return s


# ---------------------------------------------------------------------------
# Column name synonyms → our standard OUTPUT_COLUMNS names
# ---------------------------------------------------------------------------
_COL_SYNONYMS: dict[str, str] = {
    # brand
    "brand":        "brand",
    "make":         "brand",
    "manufacturer": "brand",
    "mfr":          "brand",
    "mfg":          "brand",
    "oem":          "brand",

    # model
    "model":        "model",
    "model #":      "model",
    "model#":       "model",
    "model number": "model",
    "item":         "model",
    "product":      "model",
    "product name": "model",
    "name":         "model",
    "machine":      "model",
    "unit":         "model",
    "equipment":    "model",

    # condition
    "condition":    "condition",
    "cond":         "condition",
    "grade":        "condition",
    "quality":      "condition",
    "status":       "condition",
    "passcopy":     "condition",

    # state / location
    "state":        "state",
    "location":     "state",
    "loc":          "state",
    "region":       "state",
    "warehouse":    "state",
    "wh":           "state",
    "city":         "state",
    "site":         "state",

    # inventory number
    "inv":          "inv",
    "inv #":        "inv",
    "inv#":         "inv",
    "inventory":    "inv",
    "inventory #":  "inv",
    "inventory#":   "inv",
    "item #":       "inv",
    "item no":      "inv",
    "part #":       "inv",
    "sku":          "inv",
    "tag":          "inv",
    "stock #":      "inv",
    "stock#":       "inv",
    "ref":          "inv",
    "reference":    "inv",

    # serial number
    "serial":        "serial",
    "serial_#":      "serial",
    "serial #":      "serial",
    "serial#":       "serial",
    "serial number": "serial",
    "serialnumber":  "serial",
    "sn":            "serial",
    "s/n":           "serial",

    # total meter (when source provides combined total)
    "total_meter":        "total_meter",
    "total meter":        "total_meter",
    "total":              "total_meter",
    "total copies":       "total_meter",
    "total count":        "total_meter",

    # B&W meter (also used as generic "meter" when only one meter provided)
    "meter":              "bw_meter",
    "bw_meter":           "bw_meter",
    "bw meter":           "bw_meter",
    "b&w":                "bw_meter",
    "b&w meter":          "bw_meter",
    "b&w copies":         "bw_meter",
    "bw copies":          "bw_meter",
    "black & white":      "bw_meter",
    "black and white":    "bw_meter",
    "black & white meter":"bw_meter",
    "black and white meter":"bw_meter",
    "total b&w":          "bw_meter",
    "total bw":           "bw_meter",
    "b/w meter":          "bw_meter",   # Impact uses B/W not B&W
    "b/w":                "bw_meter",
    "b/w copies":         "bw_meter",

    # Color meter
    "color_meter":        "color_meter",
    "color meter":        "color_meter",
    "colour_meter":       "color_meter",
    "colour meter":       "color_meter",
    "color copies":       "color_meter",
    "colour copies":      "color_meter",
    "clr meter":          "color_meter",
    "total color":        "color_meter",
    "total colour":       "color_meter",

    # is_color flag
    "is_color":     "is_color",
    "is color":     "is_color",
    "color type":   "is_color",
    "colour type":  "is_color",

    # feeder
    "feeder_model": "feeder_model",
    "feeder model": "feeder_model",
    "feeder type":  "feeder_model",
    "feeder":       "feeder_model",
    "adf":          "feeder_model",
    "radf":         "feeder_model",
    "dadf":         "feeder_model",
    "df":           "feeder_model",   # Impact: DF = Document Feeder column

    # capacity
    "capacity":       "capacity",
    "paper capacity": "capacity",
    "drawers":        "capacity",
    "trays":          "capacity",
    "tray config":    "capacity",
    "paper trays":    "capacity",
    "pfu":            "capacity",     # Impact: PFU = Paper Feed Unit

    # finisher
    "finisher":       "finisher",
    "finisher type":  "finisher",
    "sorter":         "finisher",
    "stapler":        "finisher",
    "fin":            "finisher",     # Impact: Fin = Finisher column

    # print
    "print":          "print_speed",
    "print speed":    "print_speed",
    "speed":          "print_speed",
    "ppm":            "print_speed",
    "prt":            "print_speed",  # Impact: Prt = Print protocol column

    # scan
    "scan":           "scan",
    "scanner":        "scan",
    "scanning":       "scan",
    "has scan":       "scan",
    "scan capable":   "scan",

    # fax
    "fax":            "fax",
    "has fax":        "fax",
    "facsimile":      "fax",
    "fax capable":    "fax",

    # qty
    "qty":          "qty",
    "quantity":     "qty",
    "stock":        "qty",
    "available":    "qty",
    "in stock":     "qty",
    "count":        "qty",
    "units":        "qty",

    # price
    "price":        "price",
    "cost":         "price",
    "rate":         "price",
    "msrp":         "price",
    "list price":   "price",
    "sell price":   "price",
    "asking":       "price",
    "amount":       "price",

    # description / accessories
    "description":       "description",
    "accessories":       "description",
    "equipment_description": "description",
    "item configuration": "description",
    "item config":        "description",
    "configuration":      "description",
    "config":             "description",

    # notes
    "notes":        "notes",
    "note":         "notes",
    "comments":     "notes",
    "comment":      "notes",
    "remarks":      "notes",
}


def _normalize_col_name(col: str) -> str:
    return _COL_SYNONYMS.get(col.lower().strip(), col.lower().strip())


# ---------------------------------------------------------------------------
# Description parsing — extract feature flags from free-text descriptions
# ---------------------------------------------------------------------------

_FEEDER_RE = re.compile(
    r"\b(RADF|DADF|SADF|ADF|SPDF|RDF|DF-\d+|DP-\d+)\b",
    re.IGNORECASE,
)
_FINISHER_RE = re.compile(
    r"\b(FINISHER|BOOKLET|STAPLE|STACKER|SR-\d+|FS-\d+|MJ-\d+|MX-F\w*|BP-F\w*|A-F\w*|FN-\d+)\b",
    re.IGNORECASE,
)
_CAPACITY_RE = re.compile(
    r"(\d+[xX]\d+(?:\+LCT)?|\d+-?DRAWER|\d+-?TRAY|LCT|LCIT)",
    re.IGNORECASE,
)
_COLOR_WORDS = re.compile(r"\bCOLOR\b", re.IGNORECASE)
_BW_WORDS    = re.compile(r"\bB&W\b|\bBLACK\b|\bMONO\b", re.IGNORECASE)
_SCAN_WORDS  = re.compile(r"\bSCAN\b", re.IGNORECASE)
_FAX_WORDS   = re.compile(r"\bFAX\b", re.IGNORECASE)
_PRINT_WORDS = re.compile(r"\bPRINT\b|\bCOPY\b|\bPRINTER\b", re.IGNORECASE)


def _parse_description(desc: str) -> dict:
    """
    Scan a free-text description string and extract feature flags/values.
    Returns a dict with keys: feeder_model, finisher, capacity, is_color, scan, fax, print_speed.
    Values are only set if detected; caller should only apply to empty fields.
    """
    if not desc or not isinstance(desc, str):
        return {}

    result: dict = {}

    feeder = _FEEDER_RE.search(desc)
    if feeder:
        result["feeder_model"] = feeder.group(0).upper()

    fin = _FINISHER_RE.search(desc)
    if fin:
        result["finisher"] = fin.group(0).title()

    cap = _CAPACITY_RE.search(desc)
    if cap:
        result["capacity"] = cap.group(0)

    if _COLOR_WORDS.search(desc):
        result["is_color"] = "YES"
    elif _BW_WORDS.search(desc):
        result.setdefault("is_color", "NO")

    if _SCAN_WORDS.search(desc):
        result["scan"] = "YES"

    if _FAX_WORDS.search(desc):
        result["fax"] = "YES"

    if _PRINT_WORDS.search(desc):
        result["print_speed"] = "YES"

    return result


def normalize(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    """
    Take a raw DataFrame (from any fetcher or manual import) and return a
    DataFrame with standardized OUTPUT_COLUMNS.
    """
    if df.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    # Rename columns to standard names
    rename_map = {col: _normalize_col_name(col) for col in df.columns}
    df = df.rename(columns=rename_map)

    # Preserve per-row source override before dropping _ columns
    per_row_source = None
    if "_raw_source" in df.columns:
        per_row_source = df["_raw_source"].copy()

    # Drop internal helper columns
    df = df.drop(columns=[c for c in df.columns if c.startswith("_")], errors="ignore")

    # --- Brand resolution ---
    if "brand" not in df.columns:
        if "model" in df.columns:
            df["brand"] = df["model"].apply(_infer_brand_from_model)
        else:
            df["brand"] = ""
    else:
        df["brand"] = df["brand"].fillna("").apply(resolve_brand)
        if "model" in df.columns:
            mask = df["brand"] == ""
            df.loc[mask, "brand"] = df.loc[mask, "model"].apply(_infer_brand_from_model)

    # Clean model: strip leading brand name if it duplicates what's in brand
    if "model" in df.columns:
        df["model"] = df.apply(_clean_model, axis=1)

    # --- Meter columns ---
    # Clean all three meter columns independently, force float dtype
    for col in ("total_meter", "bw_meter", "color_meter"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].apply(_clean_meter), errors="coerce")
        else:
            df[col] = pd.array([None] * len(df), dtype="Float64")

    # Fix swapped color/total meters: if color > total, swap them
    _clr = pd.to_numeric(df["color_meter"], errors="coerce")
    _tot = pd.to_numeric(df["total_meter"], errors="coerce")
    swap_mask = _clr.notna() & _tot.notna() & (_tot > 0) & (_clr > _tot)
    if swap_mask.any():
        df.loc[swap_mask, "total_meter"] = _clr[swap_mask].values
        df.loc[swap_mask, "color_meter"] = _tot[swap_mask].values

    # Derive total_meter from bw + color when not provided
    bw_num  = pd.to_numeric(df["bw_meter"],    errors="coerce").fillna(0)
    clr_num = pd.to_numeric(df["color_meter"], errors="coerce").fillna(0)
    tot_num = pd.to_numeric(df["total_meter"], errors="coerce")

    has_total = tot_num.notna() & (tot_num > 0)
    has_parts = (bw_num + clr_num) > 0
    mask_derive = (~has_total) & has_parts
    if mask_derive.any():
        df.loc[mask_derive, "total_meter"] = (bw_num + clr_num)[mask_derive].values

    # If we only have total, back-fill bw_meter for B&W machines (color=0)
    tot_num = pd.to_numeric(df["total_meter"], errors="coerce")  # re-read after update
    has_total = tot_num.notna() & (tot_num > 0)
    has_bw_only = df["bw_meter"].isna() & (clr_num == 0) & has_total
    if has_bw_only.any():
        df.loc[has_bw_only, "bw_meter"] = tot_num[has_bw_only].values

    # Calculate bw = total - color when bw is missing but both total and color are present
    tot_num2 = pd.to_numeric(df["total_meter"], errors="coerce")
    clr_num2 = pd.to_numeric(df["color_meter"], errors="coerce")
    bw_missing = pd.to_numeric(df["bw_meter"], errors="coerce").isna()
    calc_bw_mask = bw_missing & tot_num2.notna() & (tot_num2 > 0) & clr_num2.notna() & (clr_num2 > 0)
    if calc_bw_mask.any():
        df.loc[calc_bw_mask, "bw_meter"] = (tot_num2 - clr_num2).clip(lower=0)[calc_bw_mask].values

    # --- Combine LCT (Large Capacity Tray) into capacity ---
    # Impact and similar sources may have a separate LCT column alongside PFU/capacity.
    # Merge it here so both appear in the config string.
    if "lct" in df.columns:
        if "capacity" not in df.columns:
            df["capacity"] = ""
        def _merge_lct(row):
            lct = str(row.get("lct", "")).strip()
            if lct.lower() in ("nan", "none", ""):
                return str(row.get("capacity", "")).strip()
            cap = str(row.get("capacity", "")).strip()
            if cap.lower() in ("nan", "none", ""):
                cap = ""
            return f"{cap} + LCT" if cap else "LCT"
        df["capacity"] = df.apply(_merge_lct, axis=1)
        df = df.drop(columns=["lct"], errors="ignore")

    # --- Ensure all output columns exist ---
    for col in OUTPUT_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    # Use per-row source name if provided (e.g., ARS-CA, ARS-WA), else global
    if per_row_source is not None:
        df["source"] = per_row_source.values
    else:
        df["source"] = source_name

    # Normalize vendor names to canonical display names
    df["source"] = df["source"].apply(_normalize_vendor_name)

    # --- Qty ---
    df["qty"] = pd.to_numeric(
        df["qty"].astype(str).str.replace(r"[^\d.]", "", regex=True), errors="coerce"
    )
    df["qty"] = df["qty"].fillna(1).replace(0, 1)

    # --- Price ---
    df["price"] = pd.to_numeric(
        df["price"].astype(str).str.replace(r"[^\d.]", "", regex=True), errors="coerce"
    )

    # --- Serial ---
    df["serial"] = df["serial"].fillna("").astype(str).str.strip().str.upper()
    df["serial"] = df["serial"].replace({"NAN": "", "NONE": "", "N/A": "", "NA": ""})

    # --- is_color normalization ---
    if "is_color" in df.columns:
        def _norm_color(v):
            s = str(v).strip().upper()
            if s in ("YES", "Y", "TRUE", "1", "COLOR", "COLOUR"):
                return "YES"
            if s in ("NO", "N", "FALSE", "0", "BW", "B&W", "MONO", "BLACK"):
                return "NO"
            return ""
        df["is_color"] = df["is_color"].apply(_norm_color)

    # Normalize feeder_model: "X" (Impact presence indicator) → "ADF"
    if "feeder_model" in df.columns:
        df["feeder_model"] = df["feeder_model"].apply(
            lambda v: "ADF" if str(v).strip().upper() == "X" else v
        )

    # --- scan / fax normalization ---
    # Rule: any populated (non-empty, non-NO) value means the feature is present → "YES"
    for col in ("scan", "fax"):
        if col in df.columns:
            def _norm_yn(v):
                s = str(v).strip().upper()
                if not s or s in ("NAN", "NONE", "N/A", "NA"):
                    return ""
                if s in ("NO", "N", "FALSE", "0"):
                    return "NO"
                return "YES"   # "X", "YES", "Y", scan model name, etc. all mean present
            df[col] = df[col].apply(_norm_yn)

    # --- Parse description to fill in missing feature flags ---
    if "description" in df.columns:
        feature_cols = ["feeder_model", "finisher", "capacity", "is_color", "scan", "fax", "print_speed"]
        for idx, row in df.iterrows():
            desc = str(row.get("description", ""))
            if not desc or desc == "nan":
                continue
            parsed = _parse_description(desc)
            for feat, val in parsed.items():
                current = str(row.get(feat, "")).strip()
                if not current or current in ("", "nan", "NaN"):
                    df.at[idx, feat] = val

    # --- State normalization (uppercase 2-letter state code) ---
    if "state" in df.columns:
        df["state"] = df["state"].fillna("").astype(str).str.strip().str.upper()
        df["state"] = df["state"].replace({"NAN": "", "NONE": "", "N/A": ""})
        # Keep only 2-letter state codes to avoid full location names polluting the field
        df["state"] = df["state"].apply(lambda s: s if len(s) <= 2 else "")

    # Text columns — always string, never numeric
    _TEXT_COLS = {"source", "brand", "model", "condition", "state", "inv", "serial",
                  "is_color", "feeder_model", "capacity", "finisher", "print_speed",
                  "scan", "fax", "description", "notes"}

    # --- Fill NaN ---
    for col in OUTPUT_COLUMNS:
        if col not in df.columns:
            df[col] = ""
        if col in _TEXT_COLS:
            df[col] = df[col].fillna("").astype(str)
            # Replace pandas "nan" / "None" strings with empty
            df[col] = df[col].replace({"nan": "", "None": "", "NaN": "", "none": ""})
        elif df[col].dtype == object:
            df[col] = df[col].fillna("")
        else:
            df[col] = df[col].fillna(0)

    return df[OUTPUT_COLUMNS].reset_index(drop=True)


def _clean_meter(val) -> float | None:
    """
    Parse meter values into a plain number (or None if unreadable).
    Handles: "5K" → 5000, "24K" → 24000, "736 TOTAL" → 736,
             "75,664" → 75664, "/" or "N/A" → None.
    """
    if val is None:
        return None
    s = str(val).strip().upper()
    if not s or s in ("/", "N/A", "NA", "NAN", "NONE", ""):
        return None
    # "5K" or "24K" format
    k_match = re.match(r"^([\d.]+)\s*K$", s)
    if k_match:
        try:
            return float(k_match.group(1)) * 1000
        except ValueError:
            return None
    # Strip non-numeric suffix (e.g. " TOTAL", " BW")
    s = re.sub(r"[^\d,.].*$", "", s).strip()
    s = s.replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _infer_brand_from_model(model: str) -> str:
    """Try to extract a brand name from a model string."""
    if not model or not isinstance(model, str):
        return ""
    result = resolve_brand(model)
    if result.lower() != model.strip().lower():
        return result
    first_word = model.strip().split()[0].lower() if model.strip() else ""
    return BRAND_ALIASES.get(first_word, "")


def _clean_model(row: pd.Series) -> str:
    """Remove leading brand token from model string to avoid redundancy."""
    model = str(row.get("model", "")).strip()
    brand = str(row.get("brand", "")).strip().lower()
    if not model or not brand:
        return model
    pattern = re.compile(r"^" + re.escape(brand) + r"\s*[-:]?\s*", re.IGNORECASE)
    return pattern.sub("", model).strip()
