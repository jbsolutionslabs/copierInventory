# fetchers/equipment_recovery.py — ARS / Equipment Recovery Specialists
# Uses their internal REST API: https://app.equipmentrecovery.com/api/upload/getProduct

import requests
import pandas as pd

SOURCE_NAME = "ARS (Equipment Recovery)"

# Three physical locations
LOCATIONS = ["CA", "NJ", "WA"]
API_URL = "https://app.equipmentrecovery.com/api/upload/getProduct"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def fetch() -> pd.DataFrame:
    """
    Pull copier/wide-format inventory from ARS's REST API for all locations.
    Returns a normalized DataFrame.
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    all_records = []
    copier_classes = {"COPIER", "WIDE FORMAT", "WIDE-FORMAT"}

    def clean_num(val):
        try:
            return float(str(val or "0").replace(",", ""))
        except ValueError:
            return 0.0

    for location in LOCATIONS:
        try:
            resp = session.get(API_URL, params={"location": location}, timeout=30)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            print(f"  [ARS-{location}] Error: {exc}")
            continue

        if not payload.get("success"):
            print(f"  [ARS-{location}] API returned success=false")
            continue

        loc_count = 0
        for item in payload.get("allRecord", []):
            item_class = (item.get("Class") or "").upper().strip()
            if item_class not in copier_classes:
                continue

            all_records.append({
                "inv":         item.get("Item", ""),
                "model":       item.get("Model", ""),
                "brand":       item.get("Manufacturer", ""),
                "total_meter": clean_num(item.get("Total_Meter")),
                "color_meter": clean_num(item.get("Color_Meter")),
                "bw_meter":    clean_num(item.get("BW_Meter")),
                "description": item.get("Equipment_Description", ""),
                "serial":      item.get("Serial_Number", ""),
                "condition":   "Pass" if "passed" in str(item.get("MIssing_or_Damaged", "")).lower() else "",
                "state":       location,
                "qty":         1,
                "price":       "",
                "notes":       "",
            })
            loc_count += 1

        print(f"  [ARS-{location}] {loc_count} copiers")

    if not all_records:
        print("  [ARS] No records found across all locations.")
        return pd.DataFrame()

    df = pd.DataFrame(all_records)
    return df
