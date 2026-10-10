"""Inserts, prices, duplicates, withdrawals and pre-scoring: the radar's memory."""
import pytest

import db
import ingest
import run


@pytest.fixture
def con(tmp_path):
    return db.connect(tmp_path / "radar.db")


def row(i="1", source="pisos_com", **kw):
    base = {"listing_id": f"{source}:{i}", "source": source, "url": f"https://e/{i}", "operation": "sale",
            "title": "Piso en Armilla", "city": "granada", "municipality": "Armilla", "district": "Centro",
            "price": 150000.0, "area_m2": 90.0, "rooms": 3, "property_type": "flat", "elevator": 1,
            "description": "Exterior y luminoso", "photos": "[]"}
    return {**base, **kw}


def get(con, lid):
    return con.execute("SELECT * FROM listings WHERE listing_id = ?", (lid,)).fetchone()


def test_insert_and_reappearance(con, cfg):
    assert ingest.upsert(con, row(), cfg, "2026-10-10", "daily") == "new"
    assert ingest.upsert(con, row(), cfg, "2026-10-11", "watch") == "seen"
    r = get(con, "pisos_com:1")
    assert (r["status"], r["first_seen"], r["last_seen"], r["found_by"]) == ("New", "2026-10-10", "2026-10-11", "daily")


def test_price_change_goes_to_history(con, cfg):
    ingest.upsert(con, row(), cfg, "2026-10-10", "daily")
    assert ingest.upsert(con, row(price=142000.0), cfg, "2026-10-12", "daily") == "price"
    hist = con.execute("SELECT date, price FROM price_history WHERE listing_id='pisos_com:1' ORDER BY date").fetchall()
    assert [tuple(h) for h in hist] == [("2026-10-10", 150000.0), ("2026-10-12", 142000.0)]


def test_what_fails_is_stored_as_filtered(con, cfg):
    ingest.upsert(con, row(price=250000.0), cfg, "2026-10-10", "daily")
    r = get(con, "pisos_com:1")
    assert r["status"] == "Filtered" and "price" in r["filter_reason"]


def test_a_drop_into_budget_reactivates_it(con, cfg):
    ingest.upsert(con, row(price=210000.0), cfg, "2026-10-10", "daily")
    ingest.upsert(con, row(price=195000.0), cfg, "2026-10-11", "daily")
    assert get(con, "pisos_com:1")["status"] == "New"


def test_data_that_arrives_later_is_filled_in(con, cfg):
    ingest.upsert(con, row(elevator=None), cfg, "2026-10-10", "daily")
    assert get(con, "pisos_com:1")["to_confirm"] == "elevator"
    ingest.upsert(con, row(elevator=1), cfg, "2026-10-11", "daily")
    r = get(con, "pisos_com:1")
    assert r["elevator"] == 1 and r["to_confirm"] is None


def test_what_the_detail_page_said_is_not_undone_by_the_card(con, cfg):
    # The detail page revealed "no elevator" (run.enrich stores and filters it); the next
    # day the results card lacks the field and must not resurrect the listing.
    ingest.upsert(con, row(elevator=None, description="Extracto"), cfg, "2026-10-10", "daily")
    con.execute("UPDATE listings SET elevator=0, status='Filtered', passes=0, filter_reason='no elevator', "
                "description='Ficha completa: primer piso sin ascensor' WHERE listing_id='pisos_com:1'")
    ingest.upsert(con, row(elevator=None, description="Extracto"), cfg, "2026-10-11", "daily")
    r = get(con, "pisos_com:1")
    assert r["status"] == "Filtered" and r["elevator"] == 0


def test_same_home_on_two_portals_is_a_duplicate(con, cfg):
    ingest.upsert(con, row(), cfg, "2026-10-10", "daily")
    ingest.upsert(con, row("9", source="fotocasa", district="Another name"), cfg, "2026-10-11", "daily")
    r = get(con, "fotocasa:9")
    assert r["status"] == "Duplicate" and r["dup_of"] == "pisos_com:1"


def test_similar_figures_only_merge_when_close(con, cfg):
    ingest.upsert(con, row(lat=37.1700, lon=-3.6000), cfg, "2026-10-10", "daily")
    near = row("near", source="fotocasa", price=152000.0, area_m2=91.0, lat=37.1710, lon=-3.6005)   # ~120 m
    far = row("far", source="fotocasa", price=152000.0, area_m2=91.0, lat=37.1900, lon=-3.6000)     # ~2 km
    nocoords = row("nocoords", source="fotocasa", price=152500.0, area_m2=91.0)
    for r in (near, far, nocoords):
        ingest.upsert(con, r, cfg, "2026-10-11", "daily")
    assert get(con, "fotocasa:near")["status"] == "Duplicate"
    assert get(con, "fotocasa:far")["status"] == "New"
    assert get(con, "fotocasa:nocoords")["status"] == "New"


def test_same_portal_only_merges_identical_figures(con, cfg):
    ingest.upsert(con, row("1", source="fotocasa"), cfg, "2026-10-10", "daily")
    ingest.upsert(con, row("2", source="fotocasa"), cfg, "2026-10-10", "daily")
    ingest.upsert(con, row("3", source="fotocasa", price=151000.0), cfg, "2026-10-10", "daily")
    assert get(con, "fotocasa:2")["status"] == "Duplicate"
    assert get(con, "fotocasa:3")["status"] == "New"


def test_no_duplicate_of_a_discarded_listing(con, cfg):
    ingest.upsert(con, row(description="Piso sin ascensor"), cfg, "2026-10-10", "daily")
    ingest.upsert(con, row("9", source="fotocasa"), cfg, "2026-10-11", "daily")
    assert get(con, "fotocasa:9")["status"] == "New"


def test_when_the_original_is_withdrawn_the_copy_is_released(con, cfg):
    ingest.upsert(con, row(), cfg, "2026-10-10", "daily")
    ingest.upsert(con, row("9", source="fotocasa"), cfg, "2026-10-10", "daily")
    con.execute("UPDATE listings SET status='Withdrawn' WHERE listing_id='pisos_com:1'")
    assert ingest.recheck_duplicates(con) == (1, 1)
    r = get(con, "fotocasa:9")
    assert r["status"] == "New" and r["dup_of"] is None


def test_a_released_duplicate_carries_todays_filter(con, cfg):
    ingest.upsert(con, row(), cfg, "2026-10-10", "daily")
    ingest.upsert(con, row("9", source="fotocasa"), cfg, "2026-10-10", "daily")
    ingest.upsert(con, row("9", source="fotocasa", description="Se alquila por habitaciones"), cfg, "2026-10-11", "daily")
    assert get(con, "fotocasa:9")["status"] == "Duplicate"
    con.execute("UPDATE listings SET status='Withdrawn' WHERE listing_id='pisos_com:1'")
    ingest.recheck_duplicates(con)
    assert get(con, "fotocasa:9")["status"] == "Filtered"


def test_withdrawn_only_after_two_complete_sweeps_without_it(con, cfg):
    ingest.upsert(con, row(), cfg, "2026-10-10", "daily")
    complete = {"granada:sale"}
    assert ingest.mark_missing(con, "pisos_com", complete, set(), "2026-10-11") == []
    assert get(con, "pisos_com:1")["missing_since"] == "2026-10-11"
    assert ingest.mark_missing(con, "pisos_com", complete, set(), "2026-10-12") == ["pisos_com:1"]
    r = get(con, "pisos_com:1")
    assert r["status"] == "Withdrawn" and r["withdrawn_on"] == "2026-10-12"


def test_an_incomplete_sweep_withdraws_nothing(con, cfg):
    ingest.upsert(con, row(), cfg, "2026-10-10", "daily")
    for day in ("2026-10-11", "2026-10-12", "2026-10-13"):
        ingest.mark_missing(con, "pisos_com", {"sevilla:sale"}, set(), day)
    assert get(con, "pisos_com:1")["status"] == "New"


def test_reappears_after_withdrawn(con, cfg):
    ingest.upsert(con, row(), cfg, "2026-10-10", "daily")
    ingest.mark_missing(con, "pisos_com", {"granada:sale"}, set(), "2026-10-11")
    ingest.mark_missing(con, "pisos_com", {"granada:sale"}, set(), "2026-10-12")
    assert ingest.upsert(con, row(), cfg, "2026-10-14", "daily") == "reopened"
    r = get(con, "pisos_com:1")
    assert r["status"] == "New" and r["withdrawn_on"] is None and r["missing_since"] is None


def _market(con, cfg, n=10, ppm=2000.0):
    for i in range(n):
        ingest.upsert(con, row(f"m{i}", price=ppm * 80, area_m2=80.0, rooms=2 + i % 3, district=f"D{i}"),
                      cfg, "2026-10-10", "daily")


def test_zone_median_ignores_discards(con, cfg):
    _market(con, cfg)
    ingest.upsert(con, row("room", price=40000.0, area_m2=120.0, description="Se alquilan habitaciones para estudiantes"),
                  cfg, "2026-10-10", "daily")
    st = ingest.zone_stats(con, "sale", "Armilla", None, "2026-10-10")
    assert st["median_eur_m2"] == 2000.0 and st["n"] == 10


def test_houses_are_compared_with_houses(con, cfg):
    _market(con, cfg)
    for i in range(6):
        ingest.upsert(con, row(f"c{i}", property_type="house", price=150000.0, area_m2=150.0, rooms=4, district=f"C{i}"),
                      cfg, "2026-10-10", "daily")
    assert ingest.zone_stats(con, "sale", "Armilla", None, "2026-10-10", "house")["median_eur_m2"] == 1000.0
    assert ingest.zone_stats(con, "sale", "Armilla", None, "2026-10-10", "flat")["median_eur_m2"] == 2000.0


def test_cheap_adds_but_anomalous_subtracts(con, cfg):
    _market(con, cfg)
    ingest.upsert(con, row("bargain", price=0.8 * 2000 * 90, area_m2=90.0, rooms=4), cfg, "2026-10-10", "daily")
    ingest.upsert(con, row("odd", price=0.4 * 2000 * 90, area_m2=90.0, rooms=5), cfg, "2026-10-10", "daily")
    ingest.rescore(con, "2026-10-10")
    assert get(con, "pisos_com:bargain")["prescore"] > get(con, "pisos_com:odd")["prescore"]


def test_the_queue_takes_turns_by_area_and_operation(con, cfg):
    for i in range(5):
        ingest.upsert(con, row(f"v{i}", rooms=2 + i), cfg, "2026-10-10", "daily")
    ingest.upsert(con, row("a1", operation="rent", price=600.0), cfg, "2026-10-10", "daily")
    ingest.upsert(con, row("s1", city="sevilla", municipality="Sevilla"), cfg, "2026-10-10", "daily")
    first3 = {r["listing_id"] for r in run.queue(con)[:3]}
    assert {"pisos_com:a1", "pisos_com:s1"} <= first3
