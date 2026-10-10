"""Fotocasa (Spain): the JSON in `<script id="__initial_props__">` of the results page.

Each page carries 30 listings with price, floor area, rooms, elevator, full description
and flags for occupied, bare ownership, auction, sitting tenant and short-term rental,
which map onto exclusions.

Daily sweep: one search per locality, with price, rooms and floor-area filters in the URL.
Fast cadence: a single page per province, newest first; the locality is filtered here.

Config:
    fotocasa:
      max_pages: 40                   # per locality and operation
      slugs: {}                       # locality -> Fotocasa slug, only where the automatic
                                      # one is wrong. Automatic: "La Zubia" -> la-zubia, and
                                      # a locality named like its region -> "<name>-capital".
"""
from __future__ import annotations

import datetime as dt
import json
import re

import filters
from sources import Batch

FULL_WALK = True
BASE = "https://www.fotocasa.es"
PATH_OP = {"sale": "comprar", "rent": "alquiler"}
PAGE_SIZE = 30
_PROPS = re.compile(r'<script[^>]*id=["\']__initial_props__["\'][^>]*>(.*?)</script>', re.S)

# buildingSubtype, lowercased with non-letters removed. Those marked * were observed on
# 2026-10-10; the rest are likely variants of the same scheme.
SUBTYPES = {
    "flat": "flat",  # *
    "apartment": "flat", "groundfloor": "flat",
    "groundfloorwithgarden": "flat",  # *
    "attic": "penthouse",  # *
    "penthouse": "penthouse", "duplex": "duplex", "triplex": "duplex",
    "study": "studio",  # *
    "studio": "studio", "loft": "loft",
    "housechalet": "detached",  # *
    "countryhouse": "house",  # *
    "house": "house", "terracedhouse": "house", "semidetachedhouse": "house", "rustichouse": "house",
    "chalet": "detached", "detachedhouse": "detached", "villa": "detached",
}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", filters.norm(name)).strip("-")


def props(page: str) -> dict:
    """The first block that parses as JSON: a comment that mentions the tag must not hide
    the real script."""
    for m in _PROPS.finditer(page):
        try:
            return json.loads(m.group(1))
        except ValueError:
            continue
    return {}


def search_result(page: str) -> tuple[list[dict], int | None]:
    res = (props(page).get("initialSearch") or {}).get("result") or {}
    return res.get("realEstates") or [], (res.get("counters") or {}).get("realEstates")


def normalize(x: dict, operation: str, cfg: dict) -> dict | None:
    addr = x.get("address") or {}
    area_name, muni = filters.zone_of(addr.get("province", ""), addr.get("municipality", ""), cfg)
    if area_name is None:
        return None
    feats = {f.get("key"): f.get("value") for f in x.get("features") or [] if isinstance(f, dict)}
    sub = re.sub(r"[^a-z]", "", filters.norm(x.get("buildingSubtype") or x.get("buildingType") or ""))
    ptype = SUBTYPES.get(sub, "other")
    ts = (x.get("dateOriginal") or x.get("date") or {}).get("timestamp")
    detail = (x.get("detail") or {}).get("es-ES")
    photos = [m.get("src") for m in x.get("multimedia") or [] if isinstance(m, dict) and m.get("type") == "image"]
    return {
        "listing_id": f"fotocasa:{x['id']}",
        "source": "fotocasa",
        "url": BASE + detail if detail else None,
        "operation": operation,
        "city": area_name, "municipality": muni,
        "district": addr.get("district") or addr.get("neighborhood") or None,
        "address": x.get("location") or addr.get("neighborhood"),
        "title": f"{x.get('buildingSubtype') or 'Home'} in {addr.get('neighborhood') or muni}",
        "price": float(x["rawPrice"]) if x.get("rawPrice") else None,
        "area_m2": float(feats["surface"]) if feats.get("surface") else None,
        "rooms": int(feats["rooms"]) if feats.get("rooms") else None,
        "bathrooms": int(feats["bathrooms"]) if feats.get("bathrooms") else None,
        "floor": None,     # Fotocasa's numeric floor value is a code with no published table
        "property_type": ptype,
        # The feature list only says what is there: its absence proves nothing.
        "elevator": 1 if "elevator" in feats else None,
        "description": x.get("description") or "",
        "photos": json.dumps(photos[:5]),
        "posted": dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).date().isoformat() if ts else None,
        "lat": (x.get("coordinates") or {}).get("latitude"),
        "lon": (x.get("coordinates") or {}).get("longitude"),
        "occupied": bool(x.get("isOccupied")),
        "bare_ownership": bool(x.get("isBareOwnership")),
        "rented_with_tenants": bool(x.get("isRentedWithTenants")),
        "auction": bool(x.get("isAuctioned")),
        "temporary": bool(x.get("isTemporaryRental")),
    }


def search_url(location: str, operation: str, cfg: dict, page: int, filtered: bool = True) -> str:
    path = f"{BASE}/es/{PATH_OP[operation]}/viviendas/{location}/todas-las-zonas/l"
    if page > 1:
        path += f"/{page}"
    q = ["sortType=publicationDate"]
    if filtered:
        mins = cfg.get("minimums") or {}
        q = [f"maxPrice={cfg['budgets'][operation]}", f"minRooms={mins.get('rooms', 0)}",
             f"minSurface={mins.get('area_m2', 0)}"] + q
    return path + "?" + "&".join(q)


def _walk(http, location, operation, cfg, cap, out, seen, key):
    page = 1
    while True:
        r = http.get(search_url(location, operation, cfg, page))
        items, total = search_result(r.text)
        for x in items:
            if x.get("id") is None or x["id"] in seen:
                continue
            seen.add(x["id"])
            row = normalize(x, operation, cfg)
            if row is None:
                out.dropped += 1
            else:
                out.append(row)
        if not items or total is None or page * PAGE_SIZE >= total:
            return
        if page >= cap:
            out.incomplete.add(key)
            return
        page += 1


def locations(cfg: dict, conf: dict) -> list[tuple[str, str]]:
    """(area name, Fotocasa location slug) for every configured locality."""
    overrides = conf.get("slugs") or {}
    out = []
    for a in filters.areas(cfg):
        for m in a.get("localities") or []:
            same = filters.norm(m) == filters.norm(a.get("region"))
            out.append((a["name"], overrides.get(m) or (f"{slug(m)}-capital" if same else slug(m))))
    return out


def fetch(cfg: dict, operation: str, *, conf: dict, http, quick: bool = False,
          max_pages: int | None = None, **_) -> Batch:
    import fetchers
    out, seen = Batch(), set()
    if quick:
        for a in filters.areas(cfg):
            key = f"{a['name']}:{operation}"
            _walk(http, f"{slug(a['region'])}-provincia", operation, cfg, 1, out, seen, key)
            out.incomplete.add(key)        # one page is never complete
        return out
    cap = max_pages or conf.get("max_pages", 40)
    for area_name, loc in locations(cfg, conf):
        try:
            _walk(http, loc, operation, cfg, cap, out, seen, f"{area_name}:{operation}")
        except fetchers.Challenged:
            raise
        except Exception as e:  # noqa: BLE001 - one failing locality does not sink the area
            out.incomplete.add(f"{area_name}:{operation}")
            out.notes.append(f"fotocasa {loc}: {type(e).__name__}: {e}"[:200])
    return out
