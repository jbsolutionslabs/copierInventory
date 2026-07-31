# config.py — URLs, source definitions, brand alias map

SOURCES = {
    "rci": {
        "name": "RCI Wholesale",
        "type": "excel_download",
        "url": "http://inv.rciwholesale.com/ExcelDL/RCI_Inventory.htm",
    },
    "als": {
        "name": "ALS Copiers",
        "type": "scrape",
        "url": "https://alscopiers.com/inventory/",
    },
    "ars": {
        "name": "ARS",
        "type": "scrape",
        "url": "https://www.equipmentrecovery.com/collections/all",
    },
    "copex": {
        "name": "Copex",
        "type": "csv_download",
        "url": "https://www.copexinc.com/our-inventory/",
    },
    "rsi": {
        "name": "RSI Copiers",
        "type": "csv_download",
        "url": "https://crm.rsillc.net/bc/download.csv",
    },
    "tnt": {
        "name": "TNT Copiers",
        "type": "playwright",
        "url": "https://copierondemand1234.my.salesforce-sites.com/InventorySystem",
    },
}

# Manual-import sources (drop files into imports/ folder)
MANUAL_SOURCES = {
    "tnt":      "TNT Copiers",
    "wulff":    "Wulff Enterprises",
    "mars":     "Mars",
    "impact":   "Impact Networking",
    "intercom": "Intercom Group",
}

# ---------------------------------------------------------------------------
# Vendor (source) alias map
# Keys are lowercase; values are the canonical display name shown in the UI.
# Also handles location-suffixed variants (ARS-NJ, ARS-WA, etc.) via the
# normalizer's _normalize_vendor_name() function which strips trailing state codes.
# ---------------------------------------------------------------------------
VENDOR_ALIASES = {
    # ARS — all variants that may appear from old exports or manual imports
    "ars":                        "ARS",
    "ars-ca":                     "ARS",
    "ars-nj":                     "ARS",
    "ars-wa":                     "ARS",
    "ars-sd":                     "ARS",
    "ars-tx":                     "ARS",
    "ars-fl":                     "ARS",
    "ars-ny":                     "ARS",
    "ars (equipment recovery)":   "ARS",
    "equipment recovery":         "ARS",
    "equipment recovery specialists": "ARS",

    # RCI
    "rci":                        "RCI Wholesale",
    "rci wholesale":              "RCI Wholesale",
    "rciwholesale":               "RCI Wholesale",

    # ALS
    "als":                        "ALS Copiers",
    "als copiers":                "ALS Copiers",
    "alscopiers":                 "ALS Copiers",

    # Copex
    "copex":                      "Copex",
    "copex inc":                  "Copex",
    "copex inc.":                 "Copex",

    # RSI
    "rsi":                        "RSI Copiers",
    "rsi copiers":                "RSI Copiers",

    # TNT
    "tnt":                        "TNT Copiers",
    "tnt copiers":                "TNT Copiers",

    # Wulff
    "wulff":                      "Wulff",
    "wulff enterprises":          "Wulff",

    # Mars
    "mars":                       "Mars",

    # Impact / Intercom
    "impact":                     "Impact Networking",
    "impact networking":          "Impact Networking",
    "intercom":                   "Intercom Group",
    "intercom group":             "Intercom Group",
}

# ---------------------------------------------------------------------------
# Brand alias map
# Keys are lowercase tokens found in raw data → canonical brand name
# ---------------------------------------------------------------------------
BRAND_ALIASES = {
    # Canon
    "canon":        "Canon",
    "can":          "Canon",
    "cnon":         "Canon",
    "cnn":          "Canon",
    "canon inc":    "Canon",
    "canon inc.":   "Canon",
    "canon usa":    "Canon",
    "canon usa inc":"Canon",

    # Ricoh
    "ricoh":          "Ricoh",
    "ric":            "Ricoh",
    "rico":           "Ricoh",
    "ricoh corp":     "Ricoh",
    "ricoh company":  "Ricoh",
    "ricoh usa":      "Ricoh",

    # Ricoh sub-brands
    "savin":     "Savin",
    "sav":       "Savin",
    "lanier":    "Lanier",
    "lan":       "Lanier",
    "gestetner": "Gestetner",
    "ges":       "Gestetner",
    "nashuatec": "Nashuatec",
    "nas":       "Nashuatec",

    # Kyocera
    "kyocera":                  "Kyocera",
    "kyo":                      "Kyocera",
    "kyocera-mita":             "Kyocera",
    "kyoceramita":              "Kyocera",
    "kyocera mita":             "Kyocera",
    "kyocera document solutions": "Kyocera",
    "kyocera doc":              "Kyocera",
    "km":                       "Kyocera",  # context-dependent; overridden if "konica" present

    # Konica Minolta
    "konica":               "Konica Minolta",
    "koc":                  "Konica Minolta",
    "kon":                  "Konica Minolta",
    "minolta":              "Konica Minolta",
    "min":                  "Konica Minolta",
    "konica-minolta":       "Konica Minolta",
    "konicaminolta":        "Konica Minolta",
    "konica minolta":       "Konica Minolta",
    "kon/min":              "Konica Minolta",
    "konca":                "Konica Minolta",   # common typo
    "konca minolta":        "Konica Minolta",   # common typo
    "konica / minolta":     "Konica Minolta",
    "konica/minolta":       "Konica Minolta",
    "konica-minolta inc":   "Konica Minolta",
    "develop":              "Develop",          # KM OEM brand

    # Xerox
    "xerox":            "Xerox",
    "xrx":              "Xerox",
    "xer":              "Xerox",
    "xerox corp":       "Xerox",
    "xerox corporation":"Xerox",

    # Toshiba
    "toshiba":          "Toshiba",
    "tos":              "Toshiba",
    "tosh":             "Toshiba",
    "toshiba america":  "Toshiba",
    "toshiba tec":      "Toshiba",

    # Sharp
    "sharp":            "Sharp",
    "sha":              "Sharp",
    "shp":              "Sharp",
    "sharp electronics":"Sharp",

    # HP
    "hp":       "HP",
    "hewlett":  "HP",
    "hewlett-packard": "HP",

    # Lexmark
    "lexmark": "Lexmark",
    "lex":     "Lexmark",

    # Samsung
    "samsung": "Samsung",
    "sam":     "Samsung",

    # Brother
    "brother": "Brother",
    "bro":     "Brother",

    # Epson
    "epson": "Epson",
    "eps":   "Epson",

    # Panasonic
    "panasonic": "Panasonic",
    "pan":       "Panasonic",

    # Muratec
    "muratec": "Muratec",
    "mur":     "Muratec",

    # Kyocera OEM brands
    "copystar": "Copystar",   # Kyocera OEM / US dealer brand
    "cpy":      "Copystar",

    # OCE / Canon
    "oce": "OCE",

    # Pitney Bowes
    "pitney":        "Pitney Bowes",
    "pitney bowes":  "Pitney Bowes",
}

# =============================================================================
# Market Intelligence — Feature Flags
#
# Deployment sequence:
#   1. Deploy DB migration (additive only — zero risk)
#   2. Deploy code with HISTORY_ENGINE_ENABLED=false (dark deploy)
#   3. Set HISTORY_ENGINE_ENABLED=true for one source via HISTORY_ENABLED_SOURCES
#   4. Validate event emission, idempotency, no duplicate records
#   5. Remove HISTORY_ENABLED_SOURCES restriction to enable all sources
# =============================================================================

import os as _os

HISTORY_ENGINE_ENABLED: bool = (
    _os.environ.get("HISTORY_ENGINE_ENABLED", "false").lower() == "true"
)

# When set, only these source canonical names run through the history engine.
# None means all sources are enabled (used after full validation).
# Example env value: "RCI Wholesale,ALS Copiers"
_history_sources_env = _os.environ.get("HISTORY_ENABLED_SOURCES", "").strip()
HISTORY_ENABLED_SOURCES: list[str] | None = (
    [s.strip() for s in _history_sources_env.split(",") if s.strip()]
    if _history_sources_env
    else None
)


# =============================================================================
# Per-source event threshold configuration
#
# _default applies to any source not explicitly listed.
# Keys:
#   price_change_min_abs   — minimum absolute price change ($) to emit PRICE_CHANGED
#   price_change_min_pct   — minimum % price change to emit PRICE_CHANGED
#                            (event emits when BOTH thresholds are exceeded)
#   not_observed_min_misses — consecutive valid misses before LISTING_NOT_OBSERVED
#   not_observed_min_hours  — minimum elapsed hours since last_observed_at
#                             before the event is emitted (guards against brief
#                             source downtime that clears within the same day)
#
# A "valid miss" is a scrape run where:
#   - The source completed without error or timeout
#   - The source returned at least its minimum expected record count
#   - No source-wide inventory collapse was detected
# =============================================================================

SOURCE_EVENT_CONFIG: dict[str, dict] = {
    "_default": {
        "price_change_min_abs":    25.0,
        "price_change_min_pct":    1.0,
        "not_observed_min_misses": 2,
        "not_observed_min_hours":  3.0,
    },
    # RCI sometimes serves partial inventory on first page load; give it extra
    # headroom before declaring a listing gone.
    "RCI Wholesale": {
        "price_change_min_abs":    25.0,
        "price_change_min_pct":    1.0,
        "not_observed_min_misses": 3,
        "not_observed_min_hours":  6.0,
    },
    # ALS uses a JavaScript-rendered table; occasional render failures are common.
    "ALS Copiers": {
        "price_change_min_abs":    25.0,
        "price_change_min_pct":    1.0,
        "not_observed_min_misses": 3,
        "not_observed_min_hours":  6.0,
    },
    # Manual imports are updated infrequently; a single missed import cycle
    # should not trigger a removal event.
    "TNT Copiers": {
        "price_change_min_abs":    50.0,
        "price_change_min_pct":    2.0,
        "not_observed_min_misses": 3,
        "not_observed_min_hours":  48.0,
    },
    "Wulff": {
        "price_change_min_abs":    50.0,
        "price_change_min_pct":    2.0,
        "not_observed_min_misses": 3,
        "not_observed_min_hours":  48.0,
    },
    "Mars": {
        "price_change_min_abs":    50.0,
        "price_change_min_pct":    2.0,
        "not_observed_min_misses": 3,
        "not_observed_min_hours":  48.0,
    },
    "Impact Networking": {
        "price_change_min_abs":    50.0,
        "price_change_min_pct":    2.0,
        "not_observed_min_misses": 3,
        "not_observed_min_hours":  48.0,
    },
    "Intercom Group": {
        "price_change_min_abs":    50.0,
        "price_change_min_pct":    2.0,
        "not_observed_min_misses": 3,
        "not_observed_min_hours":  48.0,
    },
}


def get_source_event_config(source_name: str) -> dict:
    """Return event thresholds for a source, falling back to _default."""
    return SOURCE_EVENT_CONFIG.get(source_name, SOURCE_EVENT_CONFIG["_default"])


# =============================================================================
# Per-source listing identity configuration
#
# Defines which normalized field serves as the stable listing identifier
# for the (source, source_listing_id) upsert key on the listings table.
#
# Rules:
#   - Prefer a source-assigned stable ID (inventory number, item number, SKU)
#   - If no stable ID is available, use 'serial' as fallback
#   - If neither is available, a fingerprint hash is generated at runtime
#     (see identity.py::listing_fingerprint())
#
# Fingerprint formula (when no stable ID exists):
#   SHA256(brand.lower() + "::" + model.lower() + "::" + state.upper()
#          + "::" + source.lower() + "::" + str(int(meter / 5000) * 5000))
#   — rounds meter to nearest 5,000 to tolerate minor discrepancies
# =============================================================================

SOURCE_LISTING_IDENTITY: dict[str, str] = {
    "_default":        "inv",     # prefer inv (inventory/item number)
    "RCI Wholesale":   "inv",     # control_num mapped to inv by fetcher
    "ALS Copiers":     "inv",     # ALS inventory number
    "ARS":             "inv",     # Equipment Recovery item number
    "RSI Copiers":     "inv",
    "Copex":           "inv",
    "TNT Copiers":     "inv",
    "Wulff":           "inv",
    "Mars":            "inv",
    "Impact Networking": "inv",
    "Intercom Group":  "inv",
}


def get_listing_id_field(source_name: str) -> str:
    """Return the normalized field name used as source_listing_id for this source."""
    return SOURCE_LISTING_IDENTITY.get(source_name, SOURCE_LISTING_IDENTITY["_default"])


# Minimum record count expected from each auto-fetch source.
# If a scrape returns fewer records than this, the run is flagged as a
# potential source anomaly and valid-miss counters are NOT advanced.
SOURCE_MIN_EXPECTED_RECORDS: dict[str, int] = {
    "_default":      1,
    "RCI Wholesale": 50,
    "ALS Copiers":   20,
    "ARS":           30,
    "RSI Copiers":   5,
    "Copex":         5,
}


def get_source_min_records(source_name: str) -> int:
    """Return minimum expected record count for source anomaly detection."""
    return SOURCE_MIN_EXPECTED_RECORDS.get(
        source_name, SOURCE_MIN_EXPECTED_RECORDS["_default"]
    )


# =============================================================================
# Standard output columns (all fetchers/importers normalize to these)
OUTPUT_COLUMNS = [
    "source",        # vendor/wholesaler name
    "brand",
    "model",
    "condition",
    "state",         # physical location state (CA, NJ, WA …)
    "inv",           # inventory / item number / SKU
    "serial",
    "total_meter",   # total meter (bw + color)
    "color_meter",   # color-only meter
    "bw_meter",      # B&W-only meter
    "is_color",      # "YES" / "NO"
    "feeder_model",  # feeder type or model name (RADF, DADF, ADF …)
    "capacity",      # paper capacity / tray config
    "finisher",      # finisher type or name
    "print_speed",   # print capability / speed ("YES" or "45 ppm")
    "scan",          # "YES" / "NO"
    "fax",           # "YES" / "NO"
    "qty",
    "price",
    "description",   # raw description / accessories text
    "notes",
]
