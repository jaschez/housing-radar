"""pisos.com (Spain): HTML search results sorted by date, per zone.

Each result card carries price, rooms, bathrooms, floor area, floor and an excerpt; the
page's `ld+json` carries the province (`addressRegion`) and the locality
(`addressLocality`), which feed the geographic guard. The elevator is not on the card: it
stays to be confirmed unless the excerpt says it, and the detail page settles it for
whatever reaches the evaluation.

Config:
    pisos_com:
      max_pages: 150                  # per zone and operation
      zones:                          # area name -> pisos.com zone slugs
        my_area: [area_de_granada]

URL: /{venta|alquiler}/pisos-{zone}/[hasta-{budget}/]fecharecientedesde-desc/[{n}/]
The site's `hasta-` filter has slack (lets ~10 % above through), so the budget is applied
again in filters.hard_filter.
"""
from __future__ import annotations

import html as htmllib
import json
import re

import filters
from sources import Batch

FULL_WALK = True
BASE = "https://www.pisos.com"
PATH_OP = {"sale": "venta", "rent": "alquiler"}

_CARD = re.compile(r'<div id="([0-9]+\.[0-9]+)" class="ad-preview\b')
_LDJSON = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S)


def _txt(s) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(s or "")).strip()


def _num(s) -> float | None:
    m = re.search(r"\d[\d.]*(?:,\d+)?", s or "")
    if not m:
        return None
    return float(m.group(0).replace(".", "").replace(",", "."))


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def locality(name: str) -> str:
    """pisos.com calls a provincial capital "Granada Capital"."""
    return re.sub(r"\s+capital$", "", _txt(name), flags=re.I)


TYPE_WORDS = [
    ("atico", "penthouse"), ("duplex", "duplex"), ("triplex", "duplex"), ("estudio", "studio"),
    ("loft", "loft"), ("chalet", "detached"), ("villa", "detached"), ("casa", "house"),
    ("cortijo", "house"), ("piso", "flat"), ("apartamento", "flat"), ("planta baja", "flat"),
]


def property_type(title: str) -> str:
    t = filters.norm(title)
    for word, kind in TYPE_WORDS:
        if t.startswith(word):
            return kind
    return "other"


def _elevator_from_text(text: str):
    t = filters.norm(text)
    if re.search(r"\bsin ascen[sc]or\b|\bno tiene ascen[sc]or\b", t):
        return 0
    if re.search(r"\bcon ascen[sc]or\b|\bascen[sc]or\b", t):
        return 1
    return None


def ld_index(page: str) -> dict:
    """@id -> {region, locality, lat, lon} from the page's ld+json."""
    out = {}
    for block in _LDJSON.findall(page):
        try:
            d = json.loads(block)
        except ValueError:
            continue
        for item in d if isinstance(d, list) else [d]:
            if not isinstance(item, dict) or "address" not in item:
                continue
            addr = item.get("address") or {}
            geo = item.get("geo") or {}
            out[str(item.get("@id"))] = {
                "region": _txt(addr.get("addressRegion")),
                "locality": _txt(addr.get("addressLocality")),
                "lat": geo.get("latitude"), "lon": geo.get("longitude"),
            }
    return out


def parse_page(page: str) -> list[dict]:
    """Raw result cards, with the ld+json geography joined in."""
    ld = ld_index(page)
    marks = [(m.start(), m.group(1)) for m in _CARD.finditer(page)]
    cards = []
    for i, (start, ad_id) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(page)
        c = page[start:end]
        href = re.search(r'data-lnk-href="([^"]+)"', c)
        price = re.search(r'ad-preview__price">\s*([^<]+)<', c)
        title = re.search(r'class="ad-preview__title">([^<]+)<', c)
        sub = re.search(r'ad-preview__subtitle">([^<]+)<', c)
        desc = re.search(r'ad-preview__description">([^<]*)<', c)
        chars = [_txt(x) for x in re.findall(r'ad-preview__char p-sm">([^<]+)<', c)]
        photo = re.search(r'<img[^>]+src="(https://fotos\.imghs\.net/[^"]+)"', c)
        cards.append({
            "id": ad_id, "href": href.group(1) if href else None,
            "price_text": _txt(price.group(1)) if price else "",
            "title": _txt(title.group(1)) if title else "",
            "subtitle": _txt(sub.group(1)) if sub else "",
            "description": _txt(desc.group(1)) if desc else "",
            "chars": chars, "photo": photo.group(1) if photo else None,
            **ld.get(ad_id, {}),
        })
    return cards


def normalize(card: dict, operation: str, cfg: dict) -> dict | None:
    """Card -> `listings` row. None if the geographic guard cuts it."""
    area_name, muni = filters.zone_of(card.get("region", ""), locality(card.get("locality", "")), cfg)
    if area_name is None:
        return None
    rooms = baths = area = None
    floor = None
    for ch in card["chars"]:
        low = filters.norm(ch)
        if "hab" in low:
            rooms = int(_num(ch) or 0) or None
        elif "bano" in low:
            baths = int(_num(ch) or 0) or None
        elif "m2" in low or "m²" in ch:
            area = _num(ch)
        elif "planta" in low or low.startswith(("bajo", "entresuelo", "semisotano", "sotano")):
            n = _num(ch)
            floor = str(int(n)) if n is not None else low     # "1ª planta" -> "1"; "bajo"
    # "Neighbourhood (District X. Granada Capital)" -> district = Neighbourhood
    district = re.sub(r"\s*\(.*\)$", "", card.get("subtitle", "")) or None
    if district and filters.canon_locality(locality(district), cfg) == filters.canon_locality(muni, cfg):
        district = None
    text = f"{card['title']} {card['description']}"
    return {
        "listing_id": f"pisos_com:{card['id']}",
        "source": "pisos_com",
        "url": BASE + card["href"] if card.get("href") else None,
        "operation": operation,
        "city": area_name, "municipality": muni, "district": district,
        "address": card["title"],
        "title": card["title"],
        "price": _num(card["price_text"]),
        "area_m2": area, "rooms": rooms, "bathrooms": baths, "floor": floor,
        "property_type": property_type(card["title"]),
        "elevator": _elevator_from_text(text),
        "description": card["description"],
        "photos": json.dumps([card["photo"]] if card.get("photo") else []),
        "lat": _float(card.get("lat")), "lon": _float(card.get("lon")),
    }


def search_url(zone: str, operation: str, budget: int | None, page: int) -> str:
    parts = [f"{BASE}/{PATH_OP[operation]}/pisos-{zone}/"]
    if budget:
        parts.append(f"hasta-{budget}/")
    parts.append("fecharecientedesde-desc/")
    if page > 1:
        parts.append(f"{page}/")
    return "".join(parts)


def _has_next(page_html: str, page: int) -> bool:
    return re.search(rf'href="[^"]*/fecharecientedesde-desc/{page + 1}/"', page_html) is not None


def _walk(http, zone, operation, budget, cap, cfg, out, seen, key):
    page = 1
    while True:
        r = http.get(search_url(zone, operation, budget, page))
        cards = parse_page(r.text)
        for card in cards:
            if card["id"] in seen:
                continue      # date order shifts listings between pages while paginating
            seen.add(card["id"])
            row = normalize(card, operation, cfg)
            if row is None:
                out.dropped += 1
            else:
                out.append(row)
        if not cards or not _has_next(r.text, page):
            return
        if page >= cap:
            out.incomplete.add(key)
            return
        page += 1


def fetch(cfg: dict, operation: str, *, conf: dict, http, quick: bool = False,
          max_pages: int | None = None, **_) -> Batch:
    cap = 1 if quick else (max_pages or conf.get("max_pages", 60))
    budget = (cfg.get("budgets") or {}).get(operation)
    out, seen = Batch(), set()
    for area_name, zones in (conf.get("zones") or {}).items():
        for zone in [zones] if isinstance(zones, str) else zones:
            _walk(http, zone, operation, budget, cap, cfg, out, seen, f"{area_name}:{operation}")
        if quick:
            out.incomplete.add(f"{area_name}:{operation}")
    return out


def detail(url: str, *, http) -> dict:
    """Full listing (for what gets evaluated): description, features and condition."""
    r = http.get(url, allow_status=(200, 404, 410))
    if r.status_code != 200:
        return {"gone": True}
    page = r.text
    desc = re.search(r'class="description__content">(.*?)</div>', page, re.S)
    feats = {}
    for lab, val in re.findall(r'features__label">([^<]+)</span>\s*(?:<span class="features__value">([^<]*)</span>)?',
                               page):
        feats[_txt(lab).rstrip(": ").strip()] = _txt(val) or True
    text = _txt(re.sub(r"<br\s*/?>", "\n", desc.group(1))) if desc else ""
    out = {"description": text, "features": feats}
    asc = feats.get("Ascensor")
    if asc is not None:
        out["elevator"] = 0 if filters.norm(str(asc)) in ("no", "false") else 1
    else:
        guess = _elevator_from_text(text)
        if guess is not None:
            out["elevator"] = guess
    cons = filters.norm(str(feats.get("Conservación", "")))
    out["condition"] = {"reformado": "renovated", "a reformar": "needs_renovation", "buen estado": "good",
                        "en buen estado": "good", "a estrenar": "new_build"}.get(cons)
    return out
