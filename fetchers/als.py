# fetchers/als.py — ALS Copiers
# Ninja Tables data is fetched two ways (in order of preference):
#   1. Playwright: load the inventory page as a real browser, intercept the
#      Ninja Tables REST or AJAX network response — bypasses IP/bot blocks.
#   2. requests fallback (REST then AJAX nonce) for local dev without Playwright.

import asyncio
import re
import requests
import pandas as pd
from bs4 import BeautifulSoup

SOURCE_NAME    = "ALS Copiers"
INVENTORY_PAGE = "https://alscopiers.com/inventory/"
AJAX_URL       = "https://alscopiers.com/wp-admin/admin-ajax.php"
TABLE_ID       = "2992"
REST_URL       = f"https://alscopiers.com/wp-json/ninja-tables/v1/tables/{TABLE_ID}/public-data"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_NONCE_RE = re.compile(r'ninja_table_public_nonce["\s:=]+([a-f0-9]+)', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Shared row parser
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Primary: Playwright (real browser — bypasses bot/IP blocks)
# ---------------------------------------------------------------------------

async def _fetch_via_playwright() -> list:
    from playwright.async_api import async_playwright

    rows: list = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        async def on_response(resp):
            url = resp.url
            # Intercept either the REST endpoint or the AJAX endpoint
            if ("ninja-tables" in url and "public-data" in url) or \
               ("admin-ajax.php" in url and "ninja_tables" in url):
                try:
                    body = await resp.text()
                    if not body.strip():
                        return
                    import json
                    data = json.loads(body)
                    parsed = _parse_rows(data)
                    if parsed:
                        rows.extend(parsed)
                        print(f"  [ALS] Captured {len(parsed)} rows via Playwright intercept.")
                except Exception:
                    pass

        page.on("response", on_response)

        await page.goto(INVENTORY_PAGE, wait_until="networkidle", timeout=60000)
        # Give JS a moment to finish any deferred requests
        await page.wait_for_timeout(3000)

        await browser.close()

    return rows


def _fetch_playwright_sync() -> list:
    try:
        return asyncio.run(_fetch_via_playwright())
    except Exception as exc:
        print(f"  [ALS] Playwright fetch failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# Fallback: plain requests (REST then AJAX nonce)
# ---------------------------------------------------------------------------

def _get_nonce(session: requests.Session) -> str:
    resp = session.get(INVENTORY_PAGE, timeout=30)
    resp.raise_for_status()

    match = _NONCE_RE.search(resp.text)
    if match:
        return match.group(1)

    soup = BeautifulSoup(resp.text, "lxml")
    for script in soup.find_all("script"):
        m = _NONCE_RE.search(script.get_text())
        if m:
            return m.group(1)

    raise RuntimeError("[ALS] Could not find ninja_table_public_nonce on inventory page")


def _fetch_via_rest(session: requests.Session) -> list:
    resp = session.get(REST_URL, params={"per_page": 9999, "page": 1}, timeout=120)
    resp.raise_for_status()
    return _parse_rows(resp.json())


def _fetch_via_ajax(session: requests.Session) -> list:
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


def _fetch_requests_fallback() -> list:
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        rows = _fetch_via_rest(session)
        if rows:
            print(f"  [ALS] {len(rows)} rows via REST API.")
            return rows
    except Exception as e:
        print(f"  [ALS] REST failed ({e}), trying AJAX fallback…")
    try:
        rows = _fetch_via_ajax(session)
        if rows:
            print(f"  [ALS] {len(rows)} rows via AJAX.")
            return rows
    except Exception as e:
        print(f"  [ALS] AJAX fallback failed: {e}")
    return []


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch() -> pd.DataFrame:
    """
    Download ALS Copiers inventory.
    Tries Playwright first (handles bot/IP blocks), falls back to requests.
    """
    rows = _fetch_playwright_sync()

    if not rows:
        print("  [ALS] Playwright returned 0 rows, trying requests fallback…")
        rows = _fetch_requests_fallback()

    if not rows:
        print("  [ALS] Warning: 0 rows returned.")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["_raw_source"] = SOURCE_NAME
    return df
