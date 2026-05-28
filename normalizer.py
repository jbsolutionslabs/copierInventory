# normalizer.py — brand name resolution and column standardization

import re
import pandas as pd
from config import BRAND_ALIASES, OUTPUT_COLUMNS

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
    2. Token match: check if any word in `raw` is an alias.
    3. Fuzzy substring match as last resort.
    4. Return cleaned raw string if no match found.
    """
    if not raw or not isinstance(raw, str):
        return ""

    clean = raw.strip()

    # 1. Exact match
    lower = clean.lower()
    if lower in BRAND_ALIASES:
        return BRAND_ALIASES[lower]

    # 2. Token / substring match (longest alias wins)
    match = _ALIAS_PATTERN.search(lower)
    if match:
        return BRAND_ALIASES[match.group(1).lower()]

    # 3. Return original with title-casing if nothing matched
    return clean.title()


# ---------------------------------------------------------------------------
# Column name synonyms → our standard OUTPUT_COLUMNS names
# ---------------------------------------------------------------------------
_COL_SYNONYMS: dict[str, str] = {
    # source-specific ALS columns
    "make":         "brand",
    "meter":        "meter",
    "total_meter":  "meter",
    "bw_meter":     "meter",
    "accessories":  "description",
    "passcopy":     "condition",
    "comment":      "notes",
    "tag":          "sku",
    "serial":       "serial",

    # model
    "model":        "model",
    "model #":      "model",
    "model#":       "model",
    "model number": "model",
    "item":         "model",
    "description":  "description",
    "product":      "model",
    "product name": "model",
    "name":         "model",
    "machine":      "model",
    "unit":         "model",
    "equipment":    "model",

    # brand
    "brand":        "brand",
    "make":         "brand",
    "manufacturer": "brand",
    "mfr":          "brand",
    "mfg":          "brand",
    "oem":          "brand",

    # condition
    "condition":    "condition",
    "cond":         "condition",
    "grade":        "condition",
    "quality":      "condition",
    "status":       "condition",

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

    # notes
    "notes":        "notes",
    "note":         "notes",
    "comments":     "notes",
    "comment":      "notes",
    "remarks":      "notes",

    # serial number
    "serial":        "serial",
    "serial_#":      "serial",
    "serial #":      "serial",
    "serial#":       "serial",
    "serial number": "serial",
    "serialnumber":  "serial",
    "sn":            "serial",
    "s/n":           "serial",
}


def _normalize_col_name(col: str) -> str:
    return _COL_SYNONYMS.get(col.lower().strip(), col.lower().strip())


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

    # Drop internal helper columns
    df = df.drop(columns=[c for c in df.columns if c.startswith("_")], errors="ignore")

    # --- Brand resolution ---
    if "brand" not in df.columns:
        # Try to infer brand from the model column
        if "model" in df.columns:
            df["brand"] = df["model"].apply(_infer_brand_from_model)
        else:
            df["brand"] = ""
    else:
        df["brand"] = df["brand"].fillna("").apply(resolve_brand)
        # If brand is empty, try extracting from model
        if "model" in df.columns:
            mask = df["brand"] == ""
            df.loc[mask, "brand"] = df.loc[mask, "model"].apply(_infer_brand_from_model)

    # Clean model: strip leading brand name if it duplicates what's in brand
    if "model" in df.columns:
        df["model"] = df.apply(_clean_model, axis=1)

    # Ensure all output columns exist
    for col in OUTPUT_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df["source"] = source_name

    # Coerce qty to numeric where possible; default to 1 (each row = one unit)
    if "qty" in df.columns:
        df["qty"] = pd.to_numeric(df["qty"].astype(str).str.replace(r"[^\d.]", "", regex=True), errors="coerce")
        df["qty"] = df["qty"].fillna(1).replace(0, 1)  # 0 means "unknown qty" → treat as 1

    # Coerce price to numeric where possible
    if "price" in df.columns:
        df["price"] = pd.to_numeric(
            df["price"].astype(str).str.replace(r"[^\d.]", "", regex=True), errors="coerce"
        )

    # Clean meter: parse "5K" → 5000, "736 TOTAL" → 736, "75,664" → 75664
    if "meter" in df.columns:
        df["meter"] = df["meter"].apply(_clean_meter)

    # Normalize serial: strip, uppercase, clear placeholder values
    if "serial" in df.columns:
        df["serial"] = df["serial"].fillna("").astype(str).str.strip().str.upper()
        df["serial"] = df["serial"].replace({"NAN": "", "NONE": "", "N/A": "", "NA": ""})

    # Fill NaN with empty string for text columns, 0 for numeric
    for col in OUTPUT_COLUMNS:
        if df[col].dtype == object:
            df[col] = df[col].fillna("")
        else:
            df[col] = df[col].fillna(0)

    return df[OUTPUT_COLUMNS].reset_index(drop=True)


def _clean_meter(val) -> float | None:
    """
    Parse meter values into a plain integer (or None if unreadable).
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
    # Remove commas
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
    # Only return if we actually matched a known brand (not just title-cased the input)
    if result.lower() != model.strip().lower():
        return result
    # Check if the first word is a known alias
    first_word = model.strip().split()[0].lower() if model.strip() else ""
    return BRAND_ALIASES.get(first_word, "")


def _clean_model(row: pd.Series) -> str:
    """Remove leading brand token from model string to avoid redundancy."""
    model = str(row.get("model", "")).strip()
    brand = str(row.get("brand", "")).strip().lower()
    if not model or not brand:
        return model
    # Strip brand prefix (case-insensitive)
    pattern = re.compile(r"^" + re.escape(brand) + r"\s*[-:]?\s*", re.IGNORECASE)
    return pattern.sub("", model).strip()
