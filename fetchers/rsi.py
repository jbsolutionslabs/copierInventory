# fetchers/rsi.py — RSI Copiers (rsicopiers.com)
# Their CRM exposes a public CSV endpoint with no authentication.
# Columns: ITEM_#, SERIAL_#, METER, COLOR, CITY, PRICE, NEW,
#          DS, ACCESSORIES, SORT/FIN, PP, INT #, EXT #, COMMENTS, MISC ERROR CODES
#
# ITEM_# uses a brand-prefix format: CAN- = Canon, RIC- = Ricoh, etc.
# We parse the prefix to populate the brand column.

import io
import re
import requests
import pandas as pd

SOURCE_NAME = "RSI Copiers"
CSV_URL     = "https://crm.rsillc.net/bc/download.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# Prefix → canonical brand name (covers what RSI uses in their ITEM_# codes)
_PREFIX_BRAND: dict[str, str] = {
    "CAN":  "Canon",
    "RIC":  "Ricoh",
    "KYO":  "Kyocera",
    "KM":   "Konica Minolta",
    "KON":  "Konica Minolta",
    "XER":  "Xerox",
    "TOS":  "Toshiba",
    "SHA":  "Sharp",
    "HP":   "HP",
    "LEX":  "Lexmark",
    "SAM":  "Samsung",
    "BRO":  "Brother",
    "EPS":  "Epson",
    "SAV":  "Savin",
    "LAN":  "Lanier",
    "GES":  "Gestetner",
    "OKI":  "OKI",
    "MUR":  "Muratec",
    "PAN":  "Panasonic",
    "DEV":  "Develop",
    "CPY":  "Copystar",
}

_PREFIX_RE = re.compile(r'^([A-Z]+)-', re.IGNORECASE)


def _brand_from_item(item: str) -> str:
    """Extract brand from RSI's ITEM_# prefix (e.g. 'CAN-IRADVC5540I' → 'Canon')."""
    m = _PREFIX_RE.match(str(item or ""))
    if m:
        prefix = m.group(1).upper()
        return _PREFIX_BRAND.get(prefix, prefix.title())
    return ""


def _model_from_item(item: str) -> str:
    """Strip the brand prefix from ITEM_# to get the model string."""
    return _PREFIX_RE.sub("", str(item or "")).strip()


def _parse_condition(int_rating: str, ext_rating: str, pp: str) -> str:
    """Combine RSI's internal/external ratings + paper-pass into a single condition."""
    parts = []
    for val in [int_rating, ext_rating]:
        v = str(val or "").strip()
        if v and v.lower() not in ("nan", ""):
            # Strip leading number (e.g. "2- Select" → "Select")
            clean = re.sub(r'^\d+[-\s]+', '', v).strip()
            if clean:
                parts.append(clean)
    pp_val = str(pp or "").strip().lower()
    if "pass" in pp_val:
        parts.append("Pass")
    elif pp_val and pp_val not in ("nan", ""):
        parts.append(pp_val.title())
    return " / ".join(dict.fromkeys(parts))  # deduplicate while preserving order


def fetch() -> pd.DataFrame:
    """Download RSI's public inventory CSV and return a raw DataFrame."""
    session = requests.Session()
    session.headers.update(HEADERS)

    resp = session.get(CSV_URL, timeout=60)
    resp.raise_for_status()

    try:
        df = pd.read_csv(io.StringIO(resp.content.decode("utf-8", errors="replace")), dtype=str)
    except Exception as exc:
        raise RuntimeError(f"[RSI] CSV parse failed: {exc}")

    if df.empty:
        print("  [RSI] Warning: CSV returned 0 rows.")
        return pd.DataFrame()

    # Normalise column names (strip whitespace, handle variations)
    df.columns = [c.strip() for c in df.columns]

    item_col = "ITEM_#" if "ITEM_#" in df.columns else df.columns[0]

    df["brand"]  = df[item_col].apply(_brand_from_item)
    df["model"]  = df[item_col].apply(_model_from_item)

    # Condition from internal/external rating + paper-pass
    int_col = next((c for c in df.columns if "INT" in c.upper()), None)
    ext_col = next((c for c in df.columns if "EXT" in c.upper()), None)
    pp_col  = next((c for c in df.columns if c.upper() in ("PP", "PASSES PAPER")), None)

    df["condition"] = df.apply(
        lambda r: _parse_condition(
            r.get(int_col, "") if int_col else "",
            r.get(ext_col, "") if ext_col else "",
            r.get(pp_col,  "") if pp_col  else "",
        ),
        axis=1,
    )

    # Price — drop original col after extracting so the normalizer doesn't see duplicates
    price_col = next((c for c in df.columns if "PRICE" in c.upper()), None)
    if price_col:
        df["price"] = df[price_col].str.replace(r"[^\d.]", "", regex=True)
        if price_col != "price":
            df = df.drop(columns=[price_col])
    else:
        df["price"] = ""

    # Description from accessories + sort/fin
    acc_col  = next((c for c in df.columns if "ACCESS" in c.upper()), None)
    fin_col  = next((c for c in df.columns if "SORT" in c.upper() or "FIN" in c.upper()), None)
    city_col = next((c for c in df.columns if "CITY" in c.upper()), None)

    def build_desc(row: pd.Series) -> str:
        parts = []
        if acc_col:
            acc = str(row.get(acc_col, "")).strip()
            if acc and acc.lower() not in ("nan", ""):
                parts.append(acc.strip(" ,"))
        if fin_col:
            fin = str(row.get(fin_col, "")).strip()
            if fin and fin.lower() not in ("nan", "no", ""):
                parts.append(fin)
        return " · ".join(parts) if parts else ""

    df["description"] = df.apply(build_desc, axis=1)
    # Drop source columns that would cause duplicate names after normalization
    cols_to_drop = [c for c in df.columns if c.upper() in ("ACCESSORIES",)]
    df = df.drop(columns=cols_to_drop, errors="ignore")

    # Notes: include city/location + comments
    comment_col = next((c for c in df.columns if "COMMENT" in c.upper()), None)
    def build_notes(row: pd.Series) -> str:
        parts = []
        if city_col:
            city = str(row.get(city_col, "")).strip()
            if city and city.lower() != "nan":
                parts.append(f"Loc: {city}")
        if comment_col:
            note = str(row.get(comment_col, "")).strip()
            if note and note.lower() != "nan":
                parts.append(note)
        return " | ".join(parts)

    df["notes"] = df.apply(build_notes, axis=1)
    # Drop source columns that would conflict with our "notes" column after normalization
    cols_to_drop = [c for c in df.columns if "COMMENT" in c.upper()]
    df = df.drop(columns=cols_to_drop, errors="ignore")

    # Extract state from CITY column only when it contains a 2-letter US state code;
    # then drop the city column so it doesn't get mapped to state by the normalizer
    # (city names like "Cherry Hill" would otherwise map to state and then be discarded,
    # leaving state empty for all RSI records)
    _US_STATES = {
        "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
        "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
        "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
        "VA","WA","WV","WI","WY","DC",
    }
    if city_col and city_col in df.columns:
        df["state"] = df[city_col].apply(
            lambda v: v.strip().upper() if str(v).strip().upper() in _US_STATES else ""
        )
        df = df.drop(columns=[city_col], errors="ignore")

    df["qty"] = 1
    df["_raw_source"] = SOURCE_NAME
    return df
