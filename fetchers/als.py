# fetchers/als.py — ALS Copiers
# Strategy (in order):
#  1. Playwright: intercept ANY JSON response that looks like table data
#  2. Playwright: extract nonce from fully-rendered page, call AJAX with browser cookies
#  3. requests fallback: REST → AJAX nonce (works locally without IP block)

import asyncio
import json
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

_NONCE_RE = re.compile(r'ninja_table_public_nonce["\s:=\']+([a-f0-9]+)', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Shared row parser
# ---------------------------------------------------------------------------

def _parse_rows(data) -> list:
    if isinstance(data, list):
        return [item["value"] if isinstance(item, dict) and "value" in item else item
                for item in data]
    if isinstance(data, dict):
        raw = data.get("data", data.get("rows", []))
        return [item["value"] if isinstance(item, dict) and "value" in item else item
                for item in raw]
    return []


# ---------------------------------------------------------------------------
# Primary: Playwright
# ---------------------------------------------------------------------------

async def _fetch_via_playwright() -> list:
    from playwright.async_api import async_playwright

    rows: list = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        # --- Strategy 1: intercept any JSON response that looks like table data ---
        async def on_response(resp):
            if rows:  # already got data
                return
            ct = resp.headers.get("content-type", "")
            if "json" not in ct:
                return
            try:
                body = await resp.text()
                if not body.strip():
                    return
                data = json.loads(body)
                parsed = _parse_rows(data)
                if len(parsed) >= 5:
                    rows.extend(parsed)
                    print(f"  [ALS] Captured {len(parsed)} rows from {resp.url[:80]}")
            except Exception:
                pass

        page.on("response", on_response)

        try:
            await page.goto(INVENTORY_PAGE, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"  [ALS] Page load warning: {e}")

        # Wait for Ninja Tables JS to fire its data request
        await page.wait_for_timeout(6000)

        # --- Strategy 2: extract nonce from rendered HTML, call AJAX with browser cookies ---
        if not rows:
            print("  [ALS] No JSON intercepted, trying nonce from rendered page…")
            try:
                content = await page.content()
                match = _NONCE_RE.search(content)
                if match:
                    nonce = match.group(1)
                    print(f"  [ALS] Found nonce: {nonce[:8]}…")

                    # Reuse browser cookies for the AJAX call
                    cookies = await context.cookies()
                    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

                    session = requests.Session()
                    session.headers.update({
                        **HEADERS,
                        "Cookie": cookie_str,
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": INVENTORY_PAGE,
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                    })
                    params = {
                        "action":          "wp_ajax_ninja_tables_public_action",
                        "table_id":        TABLE_ID,
                        "target_action":   "get-all-data",
                        "default_sorting": "manual_sort",
                        "skip_rows":       "0",
                        "limit_rows":      "0",
                        "ninja_table_public_nonce": nonce,
                    }
                    r = session.get(AJAX_URL, params=params, timeout=60)
                    parsed = _parse_rows(r.json())
                    if parsed:
                        rows.extend(parsed)
                        print(f"  [ALS] {len(parsed)} rows via nonce+cookies.")
                else:
                    print("  [ALS] No nonce found in rendered page.")
            except Exception as e:
                print(f"  [ALS] Nonce strategy failed: {e}")

        await context.close()
        await browser.close()

    return rows


def _fetch_playwright_sync() -> list:
    try:
        return asyncio.run(_fetch_via_playwright())
    except Exception as exc:
        print(f"  [ALS] Playwright error: {exc}")
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


def _fetch_requests_fallback() -> list:
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        resp = session.get(REST_URL, params={"per_page": 9999, "page": 1}, timeout=120)
        resp.raise_for_status()
        rows = _parse_rows(resp.json())
        if rows:
            print(f"  [ALS] {len(rows)} rows via REST API.")
            return rows
    except Exception as e:
        print(f"  [ALS] REST failed ({e}), trying AJAX fallback…")

    try:
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
        rows = _parse_rows(resp.json())
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
