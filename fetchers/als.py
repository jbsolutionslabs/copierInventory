# fetchers/als.py — ALS Copiers
# Inventory is served via WordPress Ninja Tables AJAX endpoint.
# We fetch a fresh nonce from the inventory page, then call the data API.
# Ninja Tables supports skip_rows/limit_rows pagination; we page through
# in batches of PAGE_SIZE until we get a short page (end of data).

import re
import requests
import pandas as pd
from bs4 import BeautifulSoup

SOURCE_NAME    = "ALS Copiers"
INVENTORY_PAGE = "https://alscopiers.com/inventory/"
AJAX_URL       = "https://alscopiers.com/wp-admin/admin-ajax.php"
TABLE_ID       = "2992"
PAGE_SIZE      = 200   # rows per request

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


def _unwrap_rows(data) -> list:
    """Extract row dicts from Ninja Tables response (handles list or dict wrapper)."""
    if isinstance(data, list):
        return [item["value"] if isinstance(item, dict) and "value" in item else item
                for item in data]
    if isinstance(data, dict):
        raw = data.get("data", data.get("rows", []))
        return [item["value"] if isinstance(item, dict) and "value" in item else item
                for item in raw]
    return []


def fetch() -> pd.DataFrame:
    """
    Download ALS Copiers inventory via WordPress Ninja Tables AJAX API.
    Pages through the full table in batches of PAGE_SIZE to guarantee all
    records are retrieved regardless of server-side row limits.
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    nonce = _get_nonce(session)

    ajax_headers = {
        **HEADERS,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": INVENTORY_PAGE,
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }

    all_rows: list[dict] = []
    skip = 0

    while True:
        params = {
            "action":          "wp_ajax_ninja_tables_public_action",
            "table_id":        TABLE_ID,
            "target_action":   "get-all-data",
            "default_sorting": "manual_sort",
            "skip_rows":       str(skip),
            "limit_rows":      str(PAGE_SIZE),
            "ninja_table_public_nonce": nonce,
        }

        resp = session.get(AJAX_URL, params=params, headers=ajax_headers, timeout=120)
        resp.raise_for_status()

        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"[ALS] JSON parse failed (skip={skip}): {exc}\n{resp.text[:300]}")

        page_rows = _unwrap_rows(data)

        if not page_rows:
            break  # no more data

        all_rows.extend(page_rows)
        print(f"  [ALS] page skip={skip}: {len(page_rows)} rows (total so far: {len(all_rows)})")

        if len(page_rows) < PAGE_SIZE:
            break  # last page (short page = end of data)

        skip += PAGE_SIZE

    if not all_rows:
        print("  [ALS] Warning: AJAX returned 0 rows.")
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["_raw_source"] = SOURCE_NAME
    return df
