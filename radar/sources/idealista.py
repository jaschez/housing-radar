"""Idealista (Spain, Italy, Portugal). The website answers automated requests from
datacenter addresses with a DataDome interstitial (tests/fixtures/datadome-idealista.html),
so the built-in fetchers do not scrape it. Two backends, chosen with `backend`:

  alerts    (default) No requests at all. Coverage comes from the e-mail alerts Idealista
            itself sends for a saved search; fetch() only leaves a note in the daily e-mail.
  official  The official API (developers.idealista.com, access granted on request).
            Credentials IDEALISTA_API_KEY and IDEALISTA_API_SECRET in the workspace
            secrets.env. The monthly quota is small, so every call is counted in the
            `quota` table and calls stop when the cap is reached until the next month.
            NOT VERIFIED against the live API (no credentials during development); field
            names follow the v3.5 documentation.

Config:
    idealista:
      backend: official
      monthly_quota: 100
      country: es
      centers:                        # area name -> search circle (official backend)
        my_area: {center: "37.1773,-3.5986", distance_m: 9000}
"""
from __future__ import annotations

import base64
import datetime as dt
import json
from urllib.parse import quote

import requests

import filters
import workspace
from sources import Batch

TOKEN_URL = "https://api.idealista.com/oauth/token"
SEARCH_URL = "https://api.idealista.com/3.5/{country}/search"

TYPES = {"flat": "flat", "penthouse": "penthouse", "duplex": "duplex", "studio": "studio",
         "chalet": "detached", "countryHouse": "house"}


def quota_used(con, month: str) -> int:
    r = con.execute("SELECT n_calls FROM quota WHERE source='idealista' AND month=?", (month,)).fetchone()
    return r[0] if r else 0


def quota_add(con, month: str):
    con.execute("INSERT INTO quota VALUES ('idealista', ?, 1) "
                "ON CONFLICT(source, month) DO UPDATE SET n_calls = n_calls + 1", (month,))
    con.commit()


def token(key: str, secret: str) -> str:
    basic = base64.b64encode(f"{quote(key)}:{quote(secret)}".encode()).decode()
    r = requests.post(TOKEN_URL, data={"grant_type": "client_credentials", "scope": "read"},
                      headers={"Authorization": f"Basic {basic}",
                               "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                      timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def normalize(e: dict, operation: str, cfg: dict) -> dict | None:
    area_name, muni = filters.zone_of(e.get("province", ""), e.get("municipality", ""), cfg)
    if area_name is None:
        return None
    return {
        "listing_id": f"idealista:{e['propertyCode']}",
        "source": "idealista",
        "url": e.get("url"),
        "operation": operation,
        "city": area_name, "municipality": muni,
        "district": e.get("district") or e.get("neighborhood"),
        "address": e.get("address"),
        "title": f"{e.get('propertyType', '')} in {e.get('address', '')}",
        "price": float(e["price"]) if e.get("price") is not None else None,
        "area_m2": float(e["size"]) if e.get("size") else None,
        "rooms": e.get("rooms"),
        "bathrooms": e.get("bathrooms"),
        "floor": str(e["floor"]) if e.get("floor") is not None else None,
        "property_type": TYPES.get(e.get("propertyType"), "other"),
        "elevator": None if e.get("hasLift") is None else int(bool(e["hasLift"])),
        "condition": {"good": "good", "renew": "needs_renovation",
                      "newdevelopment": "new_build"}.get(e.get("status")),
        "description": e.get("description") or "",
        "photos": json.dumps([e["thumbnail"]] if e.get("thumbnail") else []),
        "lat": e.get("latitude"), "lon": e.get("longitude"),
    }


def fetch(cfg: dict, operation: str, *, conf: dict, http=None, quick: bool = False, con=None, **_) -> Batch:
    backend = conf.get("backend", "alerts")
    out = Batch()
    out.incomplete = {f"{a}:{operation}" for a in filters.area_names(cfg)}   # never a full walk
    if backend == "alerts":
        out.notes.append("Idealista: not queried (backend alerts); covered by its own e-mail alerts.")
        return out
    if backend != "official":
        raise ValueError(f"unknown Idealista backend: {backend}")
    if quick:
        return out        # the quota does not stretch to the 30-minute cadence
    env = workspace.secrets()
    key, secret = env.get("IDEALISTA_API_KEY"), env.get("IDEALISTA_API_SECRET")
    if not (key and secret):
        raise RuntimeError("backend official needs IDEALISTA_API_KEY and IDEALISTA_API_SECRET in secrets.env")
    if con is None:
        raise RuntimeError("backend official needs the database connection to keep the quota")
    month = dt.date.today().strftime("%Y-%m")
    quota = int(conf.get("monthly_quota", 100))
    mins = cfg.get("minimums") or {}
    tok = None
    for area_name, center in (conf.get("centers") or {}).items():
        if quota_used(con, month) >= quota:
            out.notes.append(f"Idealista: quota of {quota} calls used up for {month}")
            break
        tok = tok or token(key, secret)
        quota_add(con, month)
        r = requests.post(SEARCH_URL.format(country=conf.get("country", "es")),
                          headers={"Authorization": f"Bearer {tok}"}, timeout=30, data={
            "operation": operation, "propertyType": "homes", "locale": "es",
            "center": center["center"], "distance": center.get("distance_m", 10000),
            "maxPrice": cfg["budgets"][operation], "minSize": mins.get("area_m2", 0),
            "bedrooms": ",".join(str(n) for n in range(mins.get("rooms", 0), 5)),
            "order": "publicationDate", "sort": "desc", "maxItems": 50, "numPage": 1,
        })
        r.raise_for_status()
        for e in r.json().get("elementList", []):
            row = normalize(e, operation, cfg)
            if row is None:
                out.dropped += 1
            else:
                out.append(row)
    return out
