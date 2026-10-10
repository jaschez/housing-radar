"""Listing sources: built-in modules and workspace plugins, chosen in the config.

    sources:
      fotocasa: {enabled: true, max_pages: 40}          # built-in: radar/sources/fotocasa.py
      my_portal: {path: plugins/my_portal.py, ...}      # your own, in the workspace
      other: {module: some_package.portal, ...}         # or any importable module

The housing market works differently in every country, so the built-in sources (Spanish
portals) are just the ones that ship; nothing else in the radar knows about them.

Contract for a source module:

    FULL_WALK = False      # optional. True only if a daily fetch walks the WHOLE result
                           # list, which is what allows marking a missing listing as
                           # withdrawn. Leave it False if you only read the newest pages.

    def fetch(cfg, operation, *, conf, http, quick=False, max_pages=None, con=None) -> list[dict]
        cfg        the whole workspace config (areas, budgets, minimums...)
        operation  "sale" or "rent"
        conf       this source's own config block
        http       a fetchers.Client: use http.get(url, ...) for every request, so the
                   politeness gap, the level-0 detector and the fetcher chain apply
        quick      True on the 30-minute cadence: read only the newest page(s)
        max_pages  page cap per search for the daily sweep (None = the source's default)
        con        the database connection, for sources that keep a quota

    def detail(url, *, http) -> dict      # optional: full listing for the evaluation.
                                          # Keys: description, elevator, condition, gone

Each returned dict is one listing with the columns of `listings` (see db.py): at least
listing_id ("<source>:<id>"), source, url, operation, city (the area name), municipality,
price; ideally area_m2, rooms, property_type (filters.PROPERTY_TYPES), elevator, lat, lon,
description. Optional boolean flags occupied / bare_ownership / rented_with_tenants /
auction / temporary map onto exclusions. Apply the geographic guard (`filters.zone_of`)
before returning: what is not in a configured area must not leave the source.

Return a `Batch` to report completeness: `incomplete` holds "<area>:<operation>" keys
whose walk did not reach the end; `dropped` counts listings cut by the guard; `notes` are
lines for the daily e-mail. A plain list is accepted and treated as incomplete.
"""
from __future__ import annotations

import plugins


class Batch(list):
    def __init__(self, *a):
        super().__init__(*a)
        self.incomplete: set[str] = set()
        self.dropped = 0
        self.notes: list[str] = []


def as_batch(result, all_keys: set[str]) -> Batch:
    if isinstance(result, Batch):
        return result
    b = Batch(result or [])
    b.incomplete = set(all_keys)
    return b


def configured(cfg: dict) -> dict:
    return cfg.get("sources") or {}


def enabled(cfg: dict) -> list[str]:
    return [name for name, conf in configured(cfg).items() if (conf or {}).get("enabled", True)]


def load(name: str, cfg: dict):
    """The module for a configured source name."""
    conf = configured(cfg).get(name) or {}
    if conf.get("path") or conf.get("module"):
        return plugins.load(conf, "sources")
    return plugins.load(name, "sources")
