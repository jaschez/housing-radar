"""Hard filter and geographic guard. No LLM.

This decides what reaches the evaluation, not whether a home is good. Anything the listing
does not state (elevator, floor area...) does NOT discard it: it goes to `to_confirm` for
the evaluation or the visit to settle. Discarding on a missing field would hide exactly the
badly written listings, which are sometimes the cheap ones.
"""
from __future__ import annotations

import re
import unicodedata

# Canonical property types. Sources map their own vocabulary onto these.
FLAT_TYPES = frozenset({"flat", "penthouse", "duplex", "studio", "loft", "other"})
HOUSE_TYPES = frozenset({"house", "detached"})
PROPERTY_TYPES = FLAT_TYPES | HOUSE_TYPES


def norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.lower()).strip()


def canon_locality(name: str, cfg: dict) -> str:
    """Comparable locality name: accents and case folded, aliases applied."""
    n = norm(name)
    aliases = {norm(k): norm(v) for k, v in (cfg.get("locality_aliases") or {}).items()}
    return aliases.get(n, n)


def areas(cfg: dict) -> list[dict]:
    return cfg.get("areas") or []


def area_names(cfg: dict) -> list[str]:
    return [a["name"] for a in areas(cfg)]


def zone_of(region: str, locality: str, cfg: dict) -> tuple[str | None, str | None]:
    """Geographic guard. Returns (area name, canonical locality) or (None, reason).

    The region is checked BEFORE the locality: a locality name repeated in another region
    must not slip in by matching the name. (Observed: one portal resolves "granada" to La
    Granada, Barcelona.)
    """
    reg = norm(region)
    loc = canon_locality(locality, cfg)
    region_seen = False
    for a in areas(cfg):
        if norm(a.get("region")) != reg:
            continue
        region_seen = True
        for m in a.get("localities") or []:
            if canon_locality(m, cfg) == loc:
                return a["name"], m
    if not region_seen:
        return None, f"region out of scope: {region or '?'}"
    return None, f"locality not listed: {locality or '?'}"


# Structured flags some sources provide, equivalent to a configured exclusion. The text
# may not say it; the flag does.
FLAG_REASONS = {
    "occupied": "occupied",
    "bare_ownership": "bare ownership",
    "rented_with_tenants": "sold with tenants",
    "auction": "auction",
    "temporary": "temporary or room rental",
}


def words(s) -> str:
    """norm() plus punctuation removed: "Se busca, compañero/a" -> " se busca companero a "."""
    return " " + re.sub(r"[^a-z0-9]+", " ", norm(s)).strip() + " "


def text_exclusion(listing: dict, cfg: dict) -> str | None:
    text = words(" ".join(str(listing.get(k) or "") for k in ("title", "description")))
    ptype = listing.get("property_type") or "other"
    for rule in cfg.get("exclusions", []):
        applies = rule.get("applies_to")
        if applies and ptype not in applies:
            continue
        if any(_hit(text, p, cfg) for p in rule["patterns"]):
            return rule["reason"]
    return None


def _hit(text: str, pattern: str, cfg: dict) -> bool:
    """Word-prefix match ("okupad" matches "okupada"; "temporal" does not match inside
    "contemporal") that is not negated: "no necesita reforma" is not "necesita reforma".
    Negation words come from `negation_words` in the config (default: "no", "not")."""
    negations = set(cfg.get("negation_words") or ("no", "not"))
    needle = " " + words(pattern).strip()
    start = text.find(needle)
    while start != -1:
        before = text[:start].split()[-2:]
        if not negations.intersection(before):
            return True
        start = text.find(needle, start + 1)
    return False


def hard_filter(listing: dict, cfg: dict) -> dict:
    """Add passes, filter_reason and to_confirm to the listing. Does not mutate the input."""
    op = listing.get("operation")
    budget = (cfg.get("budgets") or {}).get(op)
    mins = cfg.get("minimums") or {}
    ptype = listing.get("property_type") or "other"
    to_confirm: list[str] = []
    reason = None

    price = listing.get("price")
    area = listing.get("area_m2")
    rooms = listing.get("rooms")

    if budget is None:
        reason = f"operation not searched: {op}"
    elif price is None:
        reason = "no price"
    elif price > budget:
        reason = f"price {price:.0f} > {budget}"
    elif op == "rent" and price < cfg.get("rent_floor", 0):
        reason = f"rent below floor ({price:.0f} < {cfg['rent_floor']})"
    elif ptype in set(cfg.get("excluded_property_types") or []):
        reason = f"excluded type: {ptype}"
    elif area is not None and area < mins.get("area_m2", 0):
        reason = f"area {area:.0f} < {mins['area_m2']}"
    elif rooms is not None and rooms < mins.get("rooms", 0):
        reason = f"rooms {rooms} < {mins['rooms']}"
    else:
        for flag, why in FLAG_REASONS.items():
            if listing.get(flag):
                reason = why
                break
    if reason is None and ptype in FLAT_TYPES and listing.get("elevator") == 0 and cfg.get("require_elevator", True):
        reason = "no elevator"
    if reason is None:
        reason = text_exclusion(listing, cfg)

    if area is None:
        to_confirm.append("area")
    if rooms is None:
        to_confirm.append("rooms")
    if ptype in FLAT_TYPES and listing.get("elevator") is None and cfg.get("require_elevator", True):
        to_confirm.append("elevator")

    return {**listing, "passes": int(reason is None), "filter_reason": reason,
            "to_confirm": ", ".join(to_confirm) or None}


def kind(property_type) -> str:
    """Comparison class for price per m2: a 200 m2 village house measured against the flats'
    median always comes out "35 % cheaper" than its zone."""
    return "house" if property_type in HOUSE_TYPES else "flat"


def price_per_m2(listing) -> float | None:
    p, a = listing["price"], listing["area_m2"]
    return round(p / a, 1) if p and a else None
