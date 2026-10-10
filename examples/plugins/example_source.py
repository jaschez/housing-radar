"""Example source plugin: a portal that exposes search results as JSON.

Copy it into your workspace (e.g. plugins/my_portal.py), adapt the URL and the field
mapping, and enable it in config.yaml:

    sources:
      my_portal:
        path: plugins/my_portal.py
        base_url: https://api.example-portal.test/search
        max_pages: 20

The contract is documented in radar/sources/__init__.py. The essentials: use `http.get`
for every request (politeness, wall detection and the fetcher chain apply), return
listings already passed through the geographic guard, and say whether the walk was
complete so the radar knows when a missing listing means "withdrawn".
"""
import filters
from sources import Batch

FULL_WALK = True        # this source reads every page of the results, not just the newest

TYPES = {"apartment": "flat", "penthouse": "penthouse", "house": "house", "villa": "detached"}


def fetch(cfg, operation, *, conf, http, quick=False, max_pages=None, **_):
    out = Batch()
    cap = 1 if quick else (max_pages or conf.get("max_pages", 20))
    for area in filters.areas(cfg):
        key = f"{area['name']}:{operation}"
        page = 1
        while True:
            r = http.get(conf["base_url"], params={
                "type": operation, "region": area["region"], "max_price": cfg["budgets"][operation],
                "min_rooms": cfg.get("minimums", {}).get("rooms", 0), "sort": "newest", "page": page})
            data = r.json() if hasattr(r, "json") else {}
            for item in data.get("results", []):
                area_name, locality = filters.zone_of(item.get("region"), item.get("town"), cfg)
                if area_name is None:
                    out.dropped += 1
                    continue
                out.append({
                    "listing_id": f"my_portal:{item['id']}", "source": "my_portal", "url": item.get("url"),
                    "operation": operation, "city": area_name, "municipality": locality,
                    "district": item.get("neighbourhood"), "title": item.get("title"),
                    "price": item.get("price"), "area_m2": item.get("size_m2"), "rooms": item.get("bedrooms"),
                    "property_type": TYPES.get(item.get("kind"), "other"),
                    "elevator": {True: 1, False: 0}.get(item.get("lift")),
                    "description": item.get("description") or "", "lat": item.get("lat"), "lon": item.get("lon"),
                })
            if not data.get("next_page"):
                break
            if page >= cap:
                out.incomplete.add(key)
                break
            page += 1
        if quick:
            out.incomplete.add(key)
    return out
