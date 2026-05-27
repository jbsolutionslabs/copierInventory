# fetchers/rci.py — RCI Wholesale
# Their inventory is at http://inv.rciwholesale.com/ (HTTP only — HTTPS resets).
# The page is an ASP.NET DevExpress grid; inventory rows have CSS class
# "dxgvDataRow_Material" with columns: Control#, Manufacturer, Model, Description, B&W, Color.

import subprocess
import pandas as pd
from bs4 import BeautifulSoup

SOURCE_NAME = "RCI Wholesale"
INVENTORY_URL = "http://inv.rciwholesale.com/"

# Column order as rendered in the grid
_COLUMNS = ["control_num", "brand", "model", "description", "bw_meter", "color_meter"]


def fetch() -> pd.DataFrame:
    """
    Download RCI inventory page via curl (avoids Python TLS rejection on this host)
    and parse the DevExpress grid rows.
    """
    result = subprocess.run(
        [
            "curl", "-sL", INVENTORY_URL,
            "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
            "--max-time", "60",
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"[RCI] curl failed: {result.stderr[:300]}")

    html = result.stdout
    if not html or len(html) < 500:
        raise RuntimeError(f"[RCI] Empty or very short response ({len(html)} chars)")

    soup = BeautifulSoup(html, "lxml")

    # The inventory grid
    grid = soup.select_one("#pnlMain_pnlMainContent_RCI_Inventory")
    if not grid:
        raise RuntimeError("[RCI] Inventory grid not found — page structure may have changed")

    data_rows = grid.select("tr.dxgvDataRow_Material")
    if not data_rows:
        raise RuntimeError("[RCI] No data rows found in grid")

    records = []
    for row in data_rows:
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        # Pad or trim to expected column count
        while len(cells) < len(_COLUMNS):
            cells.append("")
        record = dict(zip(_COLUMNS, cells[: len(_COLUMNS)]))
        records.append(record)

    df = pd.DataFrame(records)
    df["qty"] = 1          # each row = one physical unit
    df["price"] = ""
    df["condition"] = ""
    df["notes"] = df["control_num"].apply(lambda x: f"Control#: {x}")
    df["_raw_source"] = SOURCE_NAME
    return df
