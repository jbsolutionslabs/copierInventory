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
        "name": "ARS (Equipment Recovery)",
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
# Brand alias map
# Keys are lowercase tokens found in raw data → canonical brand name
# ---------------------------------------------------------------------------
BRAND_ALIASES = {
    # Canon
    "canon": "Canon",
    "can":   "Canon",
    "cnon":  "Canon",
    "cnn":   "Canon",

    # Ricoh
    "ricoh":  "Ricoh",
    "ric":    "Ricoh",
    "rico":   "Ricoh",

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
    "kyocera":      "Kyocera",
    "kyo":          "Kyocera",
    "kyocera-mita": "Kyocera",
    "kyoceramita":  "Kyocera",
    "km":           "Kyocera",  # context-dependent; overridden below if "konica" present

    # Konica Minolta
    "konica":          "Konica Minolta",
    "koc":             "Konica Minolta",
    "kon":             "Konica Minolta",
    "minolta":         "Konica Minolta",
    "min":             "Konica Minolta",
    "konica-minolta":  "Konica Minolta",
    "konicaminolta":   "Konica Minolta",
    "konica minolta":  "Konica Minolta",
    "develop":         "Develop",  # KM OEM brand

    # Xerox
    "xerox": "Xerox",
    "xrx":   "Xerox",
    "xer":   "Xerox",

    # Toshiba
    "toshiba": "Toshiba",
    "tos":     "Toshiba",
    "tosh":    "Toshiba",

    # Sharp
    "sharp": "Sharp",
    "sha":   "Sharp",
    "shp":   "Sharp",

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
