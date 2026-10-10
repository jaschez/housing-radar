"""From normalized listing to database: inserts, price changes, cross-portal duplicates,
withdrawals and pre-scoring. Shared by the daily sweep (run.py) and the fast cadence
(watch.py). No LLM.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics

import filters

COLUMNS = ("listing_id", "source", "url", "operation", "title", "city", "municipality", "district",
           "address", "price", "area_m2", "rooms", "bathrooms", "floor", "property_type", "elevator",
           "orientation", "condition", "year_built", "description", "photos", "posted", "lat", "lon")
# Fields a later pass may fill in when an earlier one did not bring them.
FILLABLE = ("area_m2", "rooms", "bathrooms", "floor", "elevator", "condition", "district", "posted", "lat", "lon")

# Maximum distance to call two listings with similar but not identical figures the same
# home. Without it, in a city with ~1,000 comparable flats, different flats of the same
# size and price in different neighbourhoods were merged.
DUP_RADIUS_M = 250


def meters(a_lat, a_lon, b_lat, b_lon) -> float | None:
    if None in (a_lat, a_lon, b_lat, b_lon):
        return None
    dy = (a_lat - b_lat) * 110_540
    dx = (a_lon - b_lon) * 111_320 * math.cos(math.radians((a_lat + b_lat) / 2))
    return math.hypot(dx, dy)


def find_dup(con, row: dict):
    """The same home published twice, matched against live listings that pass the filter.

    Identical figures (price, area and rooms in the same locality): duplicate; on the same
    portal, also the same district. Similar figures (+-3 m2, +-3 %) on another portal: only
    if the coordinates put them within DUP_RADIUS_M. Without area or rooms, no guess.
    """
    if not (row.get("area_m2") and row.get("rooms") and row.get("price")):
        return None
    cands = con.execute(
        """SELECT listing_id, source, price, area_m2, district, lat, lon FROM listings
           WHERE listing_id != ? AND operation = ? AND municipality = ? AND rooms = ?
             AND passes = 1 AND status NOT IN ('Withdrawn', 'Duplicate')
             AND abs(area_m2 - ?) <= 3 AND abs(price - ?) <= 0.03 * ?
           ORDER BY first_seen, listing_id""",
        (row["listing_id"], row["operation"], row["municipality"], row["rooms"],
         row["area_m2"], row["price"], row["price"])).fetchall()
    for c in cands:
        exact = c["price"] == row["price"] and c["area_m2"] == row["area_m2"]
        if c["source"] == row["source"]:
            if exact and (c["district"] or "") == (row.get("district") or ""):
                return c
            continue
        if exact:
            return c
        d = meters(c["lat"], c["lon"], row.get("lat"), row.get("lon"))
        if d is not None and d <= DUP_RADIUS_M:
            return c
    return None


def recheck_duplicates(con) -> tuple[int, int]:
    """Re-check duplicates against the current rules. Release the ones that no longer
    match anything live (the original was withdrawn, or the rule got stricter).
    Returns (checked, released)."""
    freed = 0
    rows = con.execute("SELECT * FROM listings WHERE status = 'Duplicate'").fetchall()
    for r in rows:
        dup = find_dup(con, dict(r))
        if dup is None:
            con.execute("UPDATE listings SET status = CASE WHEN passes = 1 THEN 'New' ELSE 'Filtered' END, "
                        "dup_of = NULL WHERE listing_id = ?", (r["listing_id"],))
            freed += 1
        elif dup["listing_id"] != r["dup_of"]:
            con.execute("UPDATE listings SET dup_of = ? WHERE listing_id = ?", (dup["listing_id"], r["listing_id"]))
    return len(rows), freed


def upsert(con, raw: dict, cfg: dict, today: str, found_by: str) -> str:
    """Store a listing. Returns new | price | seen | reopened."""
    row = filters.hard_filter(raw, cfg)
    old = con.execute("SELECT * FROM listings WHERE listing_id = ?", (row["listing_id"],)).fetchone()
    if old is None:
        dup = find_dup(con, row) if row["passes"] else None
        status = "Duplicate" if dup else ("New" if row["passes"] else "Filtered")
        cols = [c for c in COLUMNS if c in row] + ["passes", "filter_reason", "to_confirm", "status",
                                                   "dup_of", "first_seen", "last_seen", "found_by"]
        vals = [row[c] for c in COLUMNS if c in row] + [
            row["passes"], row["filter_reason"], row["to_confirm"], status,
            dup["listing_id"] if dup else None, today, today, found_by]
        con.execute(f"INSERT INTO listings ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
        if row.get("price") is not None:
            con.execute("INSERT INTO price_history VALUES (?, ?, ?)", (row["listing_id"], today, row["price"]))
        return "new"

    upd = {"last_seen": today, "missing_since": None, "url": row.get("url") or old["url"]}
    for c in FILLABLE:
        if old[c] is None and row.get(c) is not None:
            upd[c] = row[c]
    outcome = "seen"
    if row.get("price") is not None and old["price"] is not None and abs(row["price"] - old["price"]) >= 1:
        upd["price"] = row["price"]
        con.execute("INSERT INTO price_history VALUES (?, ?, ?)", (row["listing_id"], today, row["price"]))
        outcome = "price"
    if old["status"] == "Withdrawn":
        upd["status"] = "Evaluated" if old["fit"] else "New"
        upd["withdrawn_on"] = None
        outcome = "reopened"
    # The config can change: what has not been evaluated is filtered again. Duplicates too,
    # without changing status: if their original disappears and they are released, they
    # must come out with today's verdict, not the one from the day they were first seen.
    if old["status"] in ("New", "Filtered", "Duplicate"):
        # What is already known (e.g. the elevator stated on the detail page) wins over the
        # gap in the search-results card; otherwise every sweep would resurrect discards.
        merged = {**dict(old), **row, **{k: old[k] for k in FILLABLE if old[k] is not None},
                  **{k: v for k, v in upd.items() if k in FILLABLE}}
        if len(old["description"] or "") > len(row.get("description") or ""):
            merged["description"] = old["description"]
        again = filters.hard_filter(merged, cfg)
        upd.update(passes=again["passes"], filter_reason=again["filter_reason"], to_confirm=again["to_confirm"])
        if old["status"] == "New" and not again["passes"]:
            upd["status"] = "Filtered"
        elif old["status"] == "Filtered" and again["passes"] and not find_dup(con, merged):
            upd["status"] = "New"
    sets = ", ".join(f"{k} = ?" for k in upd)
    con.execute(f"UPDATE listings SET {sets} WHERE listing_id = ?", (*upd.values(), row["listing_id"]))
    return outcome


def mark_missing(con, source: str, complete_keys: set[str], seen: set[str], today: str) -> list[str]:
    """Listings a COMPLETE sweep did not see. The first absence is only noted
    (`missing_since`): paginating a date-sorted list while listings are published and
    removed can skip one. The second, on another day, marks it withdrawn."""
    withdrawn = []
    for r in con.execute("SELECT listing_id, city, operation, missing_since FROM listings "
                         "WHERE source = ? AND status != 'Withdrawn'", (source,)).fetchall():
        if f"{r['city']}:{r['operation']}" not in complete_keys or r["listing_id"] in seen:
            continue
        if r["missing_since"] is None:
            con.execute("UPDATE listings SET missing_since = ? WHERE listing_id = ?", (today, r["listing_id"]))
        elif r["missing_since"] < today:
            con.execute("UPDATE listings SET status = 'Withdrawn', withdrawn_on = ? WHERE listing_id = ?",
                        (today, r["listing_id"]))
            withdrawn.append(r["listing_id"])
    return withdrawn


def zone_stats(con, operation: str, municipality: str, district: str | None, today: str,
               kind: str = "flat") -> dict:
    """Median price per m2 of what passed the filter in the last 120 days, in the district
    (if the sample is big enough) or the locality. Note that searches are already capped by
    budget, so this is the median of the market this radar sees, not of the whole locality.
    Discarded listings do not count: single rooms and bare-ownership sales dragged one
    city's rent median down to 4.4 EUR/m2."""
    types = sorted(filters.HOUSE_TYPES) if kind == "house" else sorted(filters.FLAT_TYPES)

    def med(where, args):
        vals = [r[0] for r in con.execute(
            f"SELECT price / area_m2 FROM listings WHERE operation = ? AND {where} AND area_m2 > 0 "
            f"AND property_type IN ({', '.join('?' * len(types))}) "
            "AND price > 0 AND passes = 1 AND status != 'Duplicate' AND last_seen >= date(?, '-120 day')",
            (operation, *args, *types, today))]
        return (round(statistics.median(vals), 1), len(vals)) if vals else (None, 0)
    if district:
        m, n = med("municipality = ? AND district = ?", (municipality, district))
        if n >= 8:
            return {"zone": f"{district} ({municipality})", "kind": kind, "median_eur_m2": m, "n": n}
    m, n = med("municipality = ?", (municipality,))
    return {"zone": municipality, "kind": kind, "median_eur_m2": m, "n": n}


def vs_zone(row, stats: dict) -> float | None:
    ppm = filters.price_per_m2(row)
    med = stats.get("median_eur_m2")
    if not ppm or not med or stats.get("n", 0) < 5:
        return None
    return round((ppm - med) / med * 100, 1)


# Below this versus the zone, cheap stops being a bargain and becomes a suspicion: a single
# room, an unflagged bare-ownership sale, an occupied home or a typo.
ANOMALY_PCT = -45


def prescore(row, stats: dict, today: str) -> float:
    """Evaluation order, not a verdict. Cheap for the zone, confirmed elevator, known good
    condition and spare floor area add; every field to confirm subtracts, and an anomalous
    price subtracts instead of adding."""
    s = 0.0
    v = vs_zone(row, stats)
    if v is not None and v <= ANOMALY_PCT:
        s -= 2
    elif v is not None:
        s += max(-3.0, min(3.0, -v / 10))       # 20 % below the zone -> +2
    if row["elevator"] == 1:
        s += 1
    if row["condition"] in ("renovated", "new_build"):
        s += 0.5
    if row["area_m2"]:
        s += max(0.0, min(1.0, (row["area_m2"] - 70) / 50))
    if row["to_confirm"]:
        s -= 0.5 * len(row["to_confirm"].split(","))
    if row["first_seen"] == today:
        s += 0.5
    if row["posted"] and row["posted"] < _days_before(today, 365):
        s -= 1          # online for over a year: often sold or stale
    return round(s, 2)


def _days_before(day: str, n: int) -> str:
    return (dt.date.fromisoformat(day) - dt.timedelta(days=n)).isoformat()


def rescore(con, today: str, ids: list[str] | None = None) -> int:
    """Pre-score what is pending (or only `ids`). The zone median is computed once per zone."""
    q = "SELECT * FROM listings WHERE status = 'New' AND passes = 1"
    args: tuple = ()
    if ids is not None:
        if not ids:
            return 0
        q += f" AND listing_id IN ({', '.join('?' * len(ids))})"
        args = tuple(ids)
    cache: dict = {}
    rows = con.execute(q, args).fetchall()
    for r in rows:
        key = (r["operation"], r["municipality"], r["district"], filters.kind(r["property_type"]))
        if key not in cache:
            cache[key] = zone_stats(con, *key[:3], today, key[3])
        con.execute("UPDATE listings SET prescore = ? WHERE listing_id = ?",
                    (prescore(r, cache[key], today), r["listing_id"]))
    return len(rows)
