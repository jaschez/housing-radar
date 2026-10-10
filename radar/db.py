"""Local listing store (SQLite). Rows are never deleted: a listing that disappears changes
status, with a date.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import workspace

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
  listing_id TEXT PRIMARY KEY,         -- "<source>:<id in the source>"
  source TEXT, url TEXT, operation TEXT,          -- sale | rent
  city TEXT,                           -- the configured area name
  municipality TEXT, district TEXT, address TEXT,
  price REAL, area_m2 REAL, rooms INTEGER, bathrooms INTEGER, floor TEXT,
  property_type TEXT,                  -- flat|penthouse|duplex|studio|loft|house|detached|other
  elevator INTEGER,                    -- 1 | 0 | NULL = not stated
  orientation TEXT,
  condition TEXT,                      -- renovated|good|needs_renovation|new_build|NULL
  year_built INTEGER, description TEXT, photos TEXT, posted TEXT,
  passes INTEGER, filter_reason TEXT, to_confirm TEXT,
  status TEXT DEFAULT 'New',           -- New|Evaluated|Notified|Withdrawn|Duplicate|Filtered
  dup_of TEXT, prescore REAL,
  fit TEXT, priority TEXT, zone_assessment TEXT, value_assessment TEXT,
  red_flags TEXT, summary TEXT, evaluated_at TEXT,
  first_seen TEXT, last_seen TEXT,
  -- Two notification markers, on purpose: the fast cadence says "something new matches"
  -- and the daily e-mail brings the zone and value assessment. With a single marker, a
  -- listing announced by the fast cadence would never reach the daily e-mail and its
  -- assessment would be lost.
  notified_at TEXT,                    -- set by report.py
  watch_notified_at TEXT,              -- set by watch.py
  withdrawn_on TEXT, missing_since TEXT
);
CREATE TABLE IF NOT EXISTS price_history (
  listing_id TEXT, date TEXT, price REAL
);
CREATE TABLE IF NOT EXISTS runs (
  run_date TEXT, source TEXT, ok INTEGER, n_items INTEGER, challenged TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS quota (
  source TEXT, month TEXT, n_calls INTEGER, PRIMARY KEY (source, month)
);
CREATE INDEX IF NOT EXISTS listings_status ON listings(status);
CREATE INDEX IF NOT EXISTS listings_source ON listings(source);
CREATE INDEX IF NOT EXISTS listings_zone ON listings(municipality, operation);
CREATE INDEX IF NOT EXISTS price_history_listing ON price_history(listing_id);
"""

# Columns added after the first schema version. CREATE TABLE IF NOT EXISTS does not add
# them to an existing database; this does, idempotently, for old and new databases alike.
ADDED_COLUMNS = [
    ("listings", "title", "TEXT"),
    ("listings", "found_by", "TEXT"),      # daily | watch: which cadence saw it first
    ("listings", "lat", "REAL"),           # coordinates as published (sometimes approximate)
    ("listings", "lon", "REAL"),
]


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = Path(path) if path else workspace.data_dir() / "radar.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=60)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    for table, col, kind in ADDED_COLUMNS:
        have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        if col not in have:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
    con.commit()
    return con
