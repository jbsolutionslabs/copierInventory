# fetchers/copex.py — Copex Inc
# Their inventory page links to a dated CSV in wp-content/uploads.
# We fetch the inventory page, find the current CSV link, then download it.

import re
import io
import requests
import pandas as pd
from bs4 import BeautifulSoup

SOURCE_NAME   = "Copex"
INVENTORY_URL = "https://www.copexinc.com/our-inventory/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# Matches any .csv link in /wp-content/uploads/
_CSV_LINK_RE = re.compile(
    r'https://www\.copexinc\.com/wp-content/uploads/[^"\']+\.csv',
    re.IGNORECASE,
)


def _find_csv_url(session: requests.Session) -> str:
    """Fetch the inventory page and extract the current CSV download link."""
    resp = session.get(INVENTORY_URL, timeout=30)
    resp.raise_for_status()

    # Search raw HTML first (fastest)
    match = _CSV_LINK_RE.search(resp.text)
    if match:
        return match.group(0)

    # Fallback: parse with BeautifulSoup
    soup = BeautifulSoup(resp.text, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".csv" in href.lower() and "copexinc.com" in href:
            return href

    raise RuntimeError("[Copex] Could not find CSV download link on inventory page")


def fetch() -> pd.DataFrame:
    """Download Copex's dated inventory CSV and return a raw DataFrame."""
    session = requests.Session()
    session.headers.update(HEADERS)

    csv_url = _find_csv_url(session)

    resp = session.get(csv_url, timeout=60)
    resp.raise_for_status()

    # The first row is a human-readable note ("Current Inventory as of…"), skip it
    try:
        df = pd.read_csv(
            io.StringIO(resp.text),
            skiprows=1,        # skip the "Current Inventory as of…" header line
            dtype=str,
        )
    except Exception as exc:
        raise RuntimeError(f"[Copex] CSV parse failed: {exc}")

    if df.empty:
        print("  [Copex] Warning: CSV returned 0 rows.")
        return pd.DataFrame()

    # Rename columns to standard names where the mapping is clear
    col_map = {
        "#":          "sku",
        "Brand":      "brand",
        "Model":      "model",
        "Meter":      "bw_meter",
        "COLOR":      "color_meter",
        "Feeder":     "description",
        "Sort_Fin":   "notes",
        "Print":      "_print",
        "Scan":       "_scan",
        "Fax":        "_fax",
        "Paper_Feed": "_paper",
        "OEM":        "_oem",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    # Build a richer description from accessory columns
    def build_desc(row: pd.Series) -> str:
        parts = []
        feeder = str(row.get("description", "")).strip()
        if feeder and feeder.lower() not in ("nan", "ask", ""):
            parts.append(feeder)
        for col, label in [("_print","Print"), ("_scan","Scan"), ("_fax","Fax")]:
            val = str(row.get(col, "")).strip().upper()
            if val == "YES":
                parts.append(label)
        return " · ".join(parts) if parts else ""

    df["description"] = df.apply(build_desc, axis=1)

    df["qty"]   = 1
    df["price"] = ""
    df["condition"] = ""
    df["_raw_source"] = SOURCE_NAME
    return df
