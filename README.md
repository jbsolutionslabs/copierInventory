# Copier Inventory Aggregator

Pulls live copier inventory from multiple wholesaler sources, normalizes the data (including brand shorthand like `can` → Canon, `kyo` → Kyocera), and serves it through a searchable web interface hosted on GitHub Pages.

---

## How It Works

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          AUTO-FETCHED (daily)                            │
│                                                                          │
│  RCI Wholesale   ALS Copiers   ARS Equipment   Copex      RSI Copiers   │
│  (HTML scrape)   (WP AJAX)     Recovery        (CSV dl)   (CSV dl)      │
│                                (REST API)                                │
│       │               │             │              │           │         │
│       └───────────────┴─────────────┴──────────────┴───────────┘         │
│                                    │                                     │
│                        MANUAL UPLOADS (as needed)                        │
│                  TNT · Wulff · Mars · Impact · Intercom                  │
│              (upload via UI or drop files in imports/)                   │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │  python3 run.py
                                 ▼
                         normalizer.py
                   (brand aliases, column mapping)
                                 │
                 ┌───────────────┴───────────────┐
                 ▼                               ▼
    output/inventory_YYYY-MM-DD.xlsx    docs/data/inventory.json
         (local use)                    (powers the web UI)
                                                 │
                                         GitHub Pages
                                       docs/index.html
                               (search · filter · upload · refresh)
```

GitHub Actions runs `python3 run.py` every morning at 7 AM PT, commits the updated `inventory.json`, and GitHub Pages automatically deploys it.

---

## Project Structure

```
copierInventory/
├── run.py                        # Main entry point — run this
├── config.py                     # Source URLs + brand alias map
├── normalizer.py                 # Standardizes column names and brand names
├── aggregator.py                 # Merges data, writes Excel + JSON
│
├── fetchers/
│   ├── rci.py                    # RCI Wholesale (HTML scrape via curl)
│   ├── als.py                    # ALS Copiers (WordPress AJAX API)
│   ├── equipment_recovery.py     # ARS Equipment Recovery (REST API)
│   ├── copex.py                  # Copex Inc (CSV download — link found dynamically)
│   └── rsi.py                    # RSI Copiers (direct CSV from CRM endpoint)
│
├── imports/                      # Drop manual files here
│   └── (your uploaded files)     # CSV or Excel, named with source key
│
├── output/                       # Generated Excel files (gitignored)
│   └── inventory_YYYY-MM-DD.xlsx
│
├── docs/                         # GitHub Pages root
│   ├── index.html                # Web UI
│   └── data/
│       └── inventory.json        # Auto-updated data file
│
└── .github/
    └── workflows/
        └── refresh.yml           # Daily auto-refresh via GitHub Actions
```

---

## Sources

### Automated — pulled every day, no action needed

These sources are fetched automatically by GitHub Actions each morning and whenever you click **↻ Refresh Data** in the UI.

| Source | Website | Method | Approx. Units |
|--------|---------|--------|---------------|
| **RCI Wholesale** | rciwholesale.com | HTML scrape | ~1,950 |
| **ALS Copiers** | alscopiers.com | WordPress AJAX API | ~700 |
| **ARS Equipment Recovery** | equipmentrecovery.com | REST API (CA + NJ + WA) | ~2,500 |
| **Copex** | copexinc.com | CSV download (link found fresh each run) | ~1,450 |
| **RSI Copiers** | rsicopiers.com | Direct CSV from CRM | ~1,200 |

### Manual — cannot be automated

These sources either have no public inventory page, require a dealer login, or only share data by email. Files must be uploaded manually.

| Source | Why it can't be automated | How to get the file |
|--------|--------------------------|---------------------|
| **TNT Copiers** | Inventory lives in a private Salesforce system | Export from their portal at [copierondemand1234.my.salesforce-sites.com](https://copierondemand1234.my.salesforce-sites.com/InventorySystem) |
| **Wulff Enterprises** | Dealer login required to view inventory | Log in at wulffenterprises.com and download |
| **Mars** | No website — sends a weekly Excel by email | Save the attachment from Lissa Morillo's email |
| **Impact Networking** | No public inventory listing; contact-form only | Request a list from Rick Burger (320-732-7480) |
| **Intercom Group** | No inventory page exists on their site | Request a list from Kau Serrano (954-449-9008) |

**How to upload manual files** — two options:

**Option A — Web UI (recommended):**
1. Click **↑ Upload File** on the inventory site
2. Drag in the CSV or Excel file
3. Confirm the source name and click **Upload & Refresh**
4. Site updates in ~2 minutes

**Option B — Drop in the `imports/` folder:**
Name the file so it contains the source key, then run `python3 run.py`:
```
tnt_inventory_2026-05-27.xlsx      → TNT Copiers
wulff_may_2026.csv                 → Wulff Enterprises
mars.xlsx                          → Mars
impact_networking_list.csv         → Impact Networking
intercom_inventory.xlsx            → Intercom Group
```

**Duplicate protection:** If you upload the same file twice (identical content), the tool detects it and skips the duplicate — no double-counting.

---

## Running Locally

### Requirements

```bash
pip install requests beautifulsoup4 pandas openpyxl lxml
```

### Commands

```bash
# Fetch all auto sources + load any files in imports/
python3 run.py

# Fetch specific sources only
python3 run.py --source rci
python3 run.py --source rci als ars copex rsi

# Only process files in imports/ (skip web fetching)
python3 run.py --imports-only

# List all available sources
python3 run.py --list-sources
```

Output lands in `output/inventory_YYYY-MM-DD.xlsx` (local use) and `docs/data/inventory.json` (web UI).

---

## GitHub Pages Setup

### 1. Create the GitHub repo

Go to [github.com/new](https://github.com/new) and create a **public** repo (public is required for free GitHub Pages).

### 2. Push your code

```bash
git remote add origin https://github.com/YOUR_USERNAME/copierInventory.git
git push -u origin main
```

### 3. Enable GitHub Pages

- Go to your repo → **Settings** → **Pages**
- Source: **Deploy from a branch**
- Branch: `main` / Folder: `/docs`
- Click **Save**

Your site will be live at `https://YOUR_USERNAME.github.io/copierInventory/`

### 4. Trigger the first refresh (optional)

The `docs/data/inventory.json` is already included in the initial commit, so the site works immediately. To pull a fresh fetch:

- Go to your repo → **Actions** → **Refresh Inventory** → **Run workflow**

---

## Web UI

The interface is at your GitHub Pages URL. No login required to view — anyone with the link can browse inventory.

### Features

| Feature | How to use |
|---------|------------|
| **Search** | Type anything — searches brand, model, description, notes |
| **Filter** | Dropdowns for Source, Brand, and Condition |
| **Sort** | Click any column header (click again to reverse) |
| **Download CSV** | Exports the currently filtered view |
| **↑ Upload File** | Upload a manual source file — see above |
| **↻ Refresh Data** | Triggers a fresh pull of all automated sources |
| **⚙ Settings** | One-time GitHub token setup for Upload and Refresh |

### Settings (⚙) — first-time setup

Upload and Refresh both call the GitHub API, so they need a token:

1. Go to [github.com/settings/tokens/new](https://github.com/settings/tokens/new)
2. Name it (e.g. `copier-inventory`) and select scopes: **`repo`** and **`workflow`**
3. Click **Generate token** and copy it
4. Open ⚙ in the UI, fill in your GitHub username, repo name, and token
5. Click **Save** — stored only in your browser's localStorage, never sent anywhere except GitHub's own API

---

## Brand Normalization

The normalizer handles shorthand brand names found in raw data. Common mappings:

| Raw input | Normalized to |
|-----------|---------------|
| `can`, `cnon`, `CAN-` (RSI prefix) | Canon |
| `ric`, `rico`, `RIC-` | Ricoh |
| `kyo`, `kyocera-mita`, `KYO-` | Kyocera |
| `km`, `konica`, `min`, `KM-` | Konica Minolta |
| `xrx`, `xer`, `XER-` | Xerox |
| `tos`, `tosh`, `TOS-` | Toshiba |
| `sha`, `shp`, `SHA-` | Sharp |
| `lex`, `LEX-` | Lexmark |
| `sam`, `SAM-` | Samsung |
| `sav` | Savin |
| `lan` | Lanier |
| `copystar`, `cpy` | Copystar |

To add a new alias, open `config.py` and add to `BRAND_ALIASES`:

```python
BRAND_ALIASES = {
    ...
    "newshorthand": "Canonical Brand Name",
}
```

---

## Adding a New Auto-Fetch Source

1. Create `fetchers/your_source.py` with a `fetch() -> pd.DataFrame` function that returns a DataFrame with a `_raw_source` column
2. Register it in `config.py`:
   ```python
   SOURCES = {
       ...
       "mysource": {
           "name": "My Source Name",
           "type": "scrape",           # or "csv_download"
           "url": "https://example.com/inventory",
       },
   }
   ```
3. Add the dispatch in `run.py` inside `_fetch_source()`:
   ```python
   elif key == "mysource":
       from fetchers.your_source import fetch
   ```

---

## Automated Refresh Schedule

The GitHub Actions workflow runs daily at **7:00 AM Pacific Time**.

You can also trigger it manually:
- From the GitHub Actions UI: **Actions** → **Refresh Inventory** → **Run workflow**
- From the web UI: click **↻ Refresh Data** (requires ⚙ Settings configured)

To change the schedule, edit the `cron` line in `.github/workflows/refresh.yml`:
```yaml
- cron: "0 14 * * *"   # 14:00 UTC = 7:00 AM PT
```

---

## Wholesaler Contact List

| Company | Contact | Phone | Website | Status |
|---------|---------|-------|---------|--------|
| ALS Copiers | Brad Stammer | — | alscopiers.com | ✅ Automated |
| ARS Equipment Recovery | Randy Dillon | 619-889-9981 | equipmentrecovery.com | ✅ Automated |
| Copex | Frank Coen | 401-862-0760 | copexinc.com | ✅ Automated |
| RCI Wholesale | Jon Ekroth | 858-449-5864 | rciwholesale.com | ✅ Automated |
| RSI Copiers | Matt O'Connor | 772-231-1170 | rsicopiers.com | ✅ Automated |
| Impact Networking | Rick Burger | 320-732-7480 | itcopiers.com | ⚠️ Manual upload |
| Intercom Group | Kau Serrano | 954-449-9008 | intercomcopiers.com | ⚠️ Manual upload |
| Mars | Lissa Morillo | 973-777-5886 x1207 | — | ⚠️ Manual upload (weekly email) |
| TNT Copiers | Tommy Nosfinger | 813-919-8679 | tntcopiers.com | ⚠️ Manual upload |
| Wulff Enterprises | Tiffany Brown | 234-401-9621 | wulffenterprises.com | ⚠️ Manual upload |
