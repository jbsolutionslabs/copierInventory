# fetchers/als.py — ALS Copiers
# Inventory is served via WordPress Ninja Tables AJAX endpoint.
# We fetch a fresh nonce from the inventory page, then call the data API.
# limit_rows=0 is Ninja Tables' convention for "return all rows" (no pagination needed).

import re
import requests
import pandas as pd
from bs4 import BeautifulSoup

SOURCE_NAME    = "ALS Copiers"
INVENTORY_PAGE = "https://alscopiers.com/inventory/"
AJAX_URL       = "https://alscopiers.com/wp-admin/admin-ajax.php"
TABLE_ID       = "2992"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_NONCE_RE = re.compile(r'ninja_table_public_nonce["\s:=]+([a-f0-9]+)', re.IGNORECASE)


def _get_nonce(session: requests.Session) -> str:
    """Load the inventory page and extract the public nonce for Ninja Tables."""
    resp = session.get(INVENTORY_PAGE, timeout=30)
    resp.raise_for_status()

    match = _NONCE_RE.search(resp.text)
    if match:
        return match.group(1)

    # Fallback: parse from JS variable block
    soup = BeautifulSoup(resp.text, "lxml")
    for script in soup.find_all("script"):
        text = script.get_text()
        m = _NONCE_RE.search(text)
        if m:
            return m.group(1)

    raise RuntimeError("[ALS] Could not find ninja_table_public_nonce on inventory page")


REST_URL = f"https://alscopiers.com/wp-json/ninja-tables/v1/tables/{TABLE_ID}/public-data"


def _parse_rows(data) -> list:
    """Normalize various Ninja Tables response shapes into a flat list of dicts."""
    if isinstance(data, list):
        return [item["value"] if isinstance(item, dict) and "value" in item else item
                for item in data]
    if isinstance(data, dict):
        raw = data.get("data", data.get("rows", []))
        return [item["value"] if isinstance(item, dict) and "value" in item else item
                for item in raw]
    return []


def _fetch_via_rest(session: requests.Session) -> list:
    """Try the Ninja Tables REST endpoint — no nonce required."""
    resp = session.get(REST_URL, params={"per_page": 9999, "page": 1}, timeout=120)
    resp.raise_for_status()
    return _parse_rows(resp.json())


def _fetch_via_ajax(session: requests.Session) -> list:
    """Fall back to the legacy AJAX endpoint using a page-scraped nonce."""
    nonce = _get_nonce(session)
    params = {
        "action":          "wp_ajax_ninja_tables_public_action",
        "table_id":        TABLE_ID,
        "target_action":   "get-all-data",
        "default_sorting": "manual_sort",
        "skip_rows":       "0",
        "limit_rows":      "0",
        "ninja_table_public_nonce": nonce,
    }
    ajax_headers = {
        **HEADERS,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": INVENTORY_PAGE,
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    resp = session.get(AJAX_URL, params=params, headers=ajax_headers, timeout=120)
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"[ALS] JSON parse failed: {exc}\nResponse: {resp.text[:500]}")
    return _parse_rows(data)


def fetch() -> pd.DataFrame:
    """
    Download ALS Copiers inventory. Tries the Ninja Tables REST API first
    (no nonce needed); falls back to the legacy AJAX nonce approach.
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    rows = []
    try:
        rows = _fetch_via_rest(session)
        if rows:
            print(f"  [ALS] {len(rows)} rows via REST API.")
    except Exception as e:
        print(f"  [ALS] REST failed ({e}), trying AJAX fallback…")

    if not rows:
        rows = _fetch_via_ajax(session)
        if rows:
            print(f"  [ALS] {len(rows)} rows via AJAX.")

    if not rows:
        print("  [ALS] Warning: 0 rows returned.")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["_raw_source"] = SOURCE_NAME
    return df
