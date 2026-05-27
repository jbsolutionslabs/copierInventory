# Copier Inventory Aggregator

Pulls live copier inventory from multiple wholesaler sources, normalizes the data (including brand shorthand like `can` → Canon, `kyo` → Kyocera), and serves it through a searchable web interface hosted on GitHub Pages.

---

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│                        Data Sources                             │
│                                                                 │
│  RCI Wholesale      ALS Copiers      ARS Equipment Recovery     │
│  (HTML scrape)      (WP AJAX API)    (REST API — CA/NJ/WA)     │
│       │                  │                     │                │
│       └──────────────────┴─────────────────────┘                │
│                           │                                     │
│              imports/ folder (manual uploads)                   │
│         TNT · Wulff · Mars · RSI · Copex · etc.                 │
└───────────────────────────┬─────────────────────────────────────┘
                            │  python3 run.py
                            ▼
                    normalizer.py
              (brand aliases, column mapping)
                            │
              ┌─────────────┴──────────────┐
              ▼                            ▼
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
│   └── equipment_recovery.py     # ARS Equipment Recovery (REST API)
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

### Auto-fetched (no setup needed)

| Source | Method | Notes |
|--------|--------|-------|
| **RCI Wholesale** | HTML scrape | ~1,900+ units, updated twice daily |
| **ALS Copiers** | WordPress AJAX API | ~700 units |
| **ARS Equipment Recovery** | REST API | ~2,400+ units across CA, NJ, WA locations |

### Manual import (drop files in `imports/`)

| Source | How to get the file |
|--------|---------------------|
| **TNT Copiers** | Salesforce-based — export manually from their system |
| **Wulff Enterprises** | Dealer login required — download after logging in |
| **Mars** | Sends weekly Excel by email |
| **RSI** | Sends daily Excel by email |
| **Copex** | Contact Frank Coen |
| **Impact Networking** | Contact Rick Burger |
| **Intercom Group** | Contact Kau Serrano |

**File naming:** Include the source name somewhere in the filename so the tool can identify it automatically:

```
tnt_inventory_2026-05-27.xlsx      → TNT Copiers
wulff_may_2026.csv                 → Wulff Enterprises
mars.xlsx                          → Mars
rsi_daily_export.csv               → RSI
```

---

## Running Locally

### Requirements

```bash
pip install requests beautifulsoup4 pandas openpyxl lxml
```

### Commands

```bash
# Fetch all sources + load any files in imports/
python3 run.py

# Fetch specific sources only
python3 run.py --source rci
python3 run.py --source rci als ars

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

The `docs/data/inventory.json` is already included in the repo from the initial commit, so the site works immediately. To pull a fresh fetch:

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
| **Upload File** | See below |
| **Refresh Data** | See below |

### Upload File (↑ button)

Uploads a manual import file (TNT, Wulff, Mars, RSI, etc.) directly from the browser:

1. Click **↑ Upload File**
2. Drag-and-drop a CSV or Excel file, or click to browse
3. Confirm the source name (auto-detected from filename, editable)
4. Click **Upload & Refresh**

The file is committed to the repo's `imports/` folder via the GitHub API, then a refresh is triggered automatically. The site updates in about 2 minutes.

**Duplicate protection:** If you upload the exact same file twice (same content), the tool detects it and skips the second upload.

### Refresh Data (↻ button)

Triggers the GitHub Actions workflow manually — useful if you want to pull a fresh fetch outside the daily schedule.

### Settings (⚙ button)

First-time setup for Upload and Refresh. You'll need a GitHub Personal Access Token:

1. Go to [github.com/settings/tokens/new](https://github.com/settings/tokens/new)
2. Give it a name (e.g. `copier-inventory`)
3. Select scopes: **`repo`** and **`workflow`**
4. Click **Generate token** and copy it
5. Open ⚙ Settings in the UI, enter your GitHub username, repo name, and token
6. Click **Save** — the token is stored only in your browser's localStorage

---

## Brand Normalization

The normalizer handles shorthand brand names automatically. Common mappings from `config.py`:

| Input (raw data) | Normalized to |
|------------------|---------------|
| `can`, `cnon` | Canon |
| `ric`, `rico` | Ricoh |
| `kyo`, `kyocera-mita` | Kyocera |
| `km`, `konica`, `min` | Konica Minolta |
| `xrx`, `xer` | Xerox |
| `tos`, `tosh` | Toshiba |
| `sha`, `shp` | Sharp |
| `lex` | Lexmark |
| `sam` | Samsung |
| `sav` | Savin |
| `lan` | Lanier |

To add a new alias, open `config.py` and add an entry to `BRAND_ALIASES`:

```python
BRAND_ALIASES = {
    ...
    "newshorthand": "Canonical Brand Name",
}
```

---

## Adding a New Auto-Fetch Source

1. Create `fetchers/your_source.py` with a `fetch() -> pd.DataFrame` function
2. Add the source to `SOURCES` in `config.py`:
   ```python
   SOURCES = {
       ...
       "mysource": {
           "name": "My Source Name",
           "type": "scrape",
           "url": "https://example.com/inventory",
       },
   }
   ```
3. Add the import and dispatch in `run.py` inside `_fetch_source()`:
   ```python
   elif key == "mysource":
       from fetchers.your_source import fetch
   ```

---

## Automated Refresh Schedule

The GitHub Actions workflow (`.github/workflows/refresh.yml`) runs daily at **7:00 AM Pacific Time**.

You can also trigger it manually:
- From the GitHub Actions UI: **Actions** → **Refresh Inventory** → **Run workflow**
- From the web UI: click **↻ Refresh Data** (requires GitHub token configured in ⚙ Settings)

To change the schedule, edit the `cron` line in `refresh.yml`:
```yaml
- cron: "0 14 * * *"   # 14:00 UTC = 7:00 AM PT
```

---

## Wholesaler Contact List

| Company | Contact | Phone | Website |
|---------|---------|-------|---------|
| ALS | Brad Stammer | — | alscopiers.com |
| ARS | Randy Dillon | 619-889-9981 | equipmentrecovery.com |
| Copex | Frank Coen | 401-862-0760 | copexinc.com |
| Impact Networking | Rick Burger | 320-732-7480 | — |
| Intercom Group | Kau Serrano | 954-449-9008 | — |
| Mars | Lissa Morillo | 973-777-5886 x1207 | — |
| RCI | Jon Ekroth | 858-449-5864 | rciwholesale.com |
| RSI | Matt O'Connor | 772-231-1170 | — |
| TNT | Tommy Nosfinger | 813-919-8679 | tntcopiers.com |
| Wulff Enterprises | Tiffany Brown | 234-401-9621 | wulffenterprises.com |
