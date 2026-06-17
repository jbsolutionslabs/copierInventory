# fetchers/tnt.py — TNT Copiers inventory via Salesforce Aura API
#
# The page uses Lightning Web Components with an Aura backend.
# We use Playwright to load the page, intercept the Aura POST response,
# and extract all inventory records in one shot (~500+ units).
#
# Requires:  pip install playwright  &&  python -m playwright install chromium

import asyncio
import json
import re
import pandas as pd

URL = "https://copierondemand1234.my.salesforce-sites.com/InventorySystem"


def _clean_meter(val) -> float | None:
    """Parse "61K" → 61000, "96K" → 96000, 106000 → 106000."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val) if val else None
    s = str(val).strip().upper().replace(",", "")
    if not s or s in ("NONE", "N/A", ""):
        return None
    k = re.match(r"^([\d.]+)\s*K$", s)
    if k:
        try:
            return float(k.group(1)) * 1000
        except ValueError:
            return None
    try:
        return float(re.sub(r"[^\d.]", "", s))
    except ValueError:
        return None


async def _fetch_async() -> list[dict]:
    """Load the page, capture the Aura inventory response, return raw records."""
    from playwright.async_api import async_playwright

    records: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        async def on_response(resp):
            if "aura" in resp.url and resp.request.method == "POST":
                try:
                    body = await resp.text()
                    data = json.loads(body)
                    for action in data.get("actions", []):
                        rv = (action.get("returnValue") or {}).get("returnValue", [])
                        if isinstance(rv, list) and rv and isinstance(rv[0], dict) and "Product2" in rv[0]:
                            records.extend(rv)
                except Exception:
                    pass

        page.on("response", on_response)

        await page.goto(URL, timeout=40000)
        # Wait for Aura to finish loading inventory
        await page.wait_for_timeout(8000)

        await browser.close()

    return records


def fetch() -> pd.DataFrame:
    """
    Fetch TNT Copiers inventory.
    Returns a raw DataFrame ready for normalizer.normalize().
    """
    try:
        raw_records = asyncio.run(_fetch_async())
    except Exception as exc:
        print(f"  [TNT] Playwright error: {exc}")
        return pd.DataFrame()

    if not raw_records:
        print("  [TNT] No records captured — Aura response may have changed.")
        return pd.DataFrame()

    rows = []
    for entry in raw_records:
        p2 = entry.get("Product2") or {}

        bw_raw    = p2.get("BW_Meter__c")
        clr_raw   = p2.get("Color_Meter__c")
        tot_raw   = p2.get("Total_Meter__c")
        bw_val    = _clean_meter(bw_raw)
        clr_val   = _clean_meter(clr_raw)
        tot_val   = _clean_meter(tot_raw)

        # Derive total if not explicitly provided
        if not tot_val and (bw_val or clr_val):
            tot_val = (bw_val or 0) + (clr_val or 0)

        feeder_raw = p2.get("Doc_Feeder__c") or ""
        # Treat "Yes" as generic ADF; otherwise use the actual model name
        feeder = "" if feeder_raw.strip().lower() in ("none", "no", "false", "") else feeder_raw.strip()
        if feeder.lower() == "yes":
            feeder = "ADF"

        fax_raw = str(p2.get("Fax__c") or "").strip().lower()
        fax = "YES" if fax_raw in ("true", "yes", "1") else ("NO" if fax_raw in ("false", "no", "0") else "")

        finisher_raw = str(p2.get("Finisher__c") or "").strip()
        finisher = "" if finisher_raw.lower() in ("none", "no", "false", "") else finisher_raw

        capacity = str(p2.get("PFU_Base_Type__c") or "").strip()

        # Color detection from color meter or model
        is_color = ""
        if clr_val and clr_val > 0:
            is_color = "YES"
        elif clr_raw == "" or clr_raw is None:
            is_color = "NO"

        rows.append({
            "brand":        p2.get("Make__c", ""),
            "model":        p2.get("Model__c", ""),
            "serial":       p2.get("Serial_Number__c") or p2.get("Name", ""),
            "total_meter":  tot_val,
            "color_meter":  clr_val,
            "bw_meter":     bw_val,
            "is_color":     is_color,
            "feeder_model": feeder,
            "finisher":     finisher,
            "capacity":     capacity,
            "fax":          fax,
            "scan":         "",       # not in API response
            "print_speed":  "YES",    # all units are printers/copiers
            "price":        entry.get("UnitPrice", ""),
            "condition":    "",
            "state":        "",
            "inv":          "",
            "notes":        "",
            "description":  "",
        })

    print(f"  [TNT] {len(rows)} records fetched.")
    return pd.DataFrame(rows)
