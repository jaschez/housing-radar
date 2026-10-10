"""Built-in Spanish sources against pages captured on 2026-10-10, and the source contract.

What they defend: price, floor area and rooms come from where the portal puts them, and
the geographic guard cuts what is outside the configured areas BEFORE it reaches the
database.
"""
import copy
from pathlib import Path

import pytest

import fetchers
from sources import Batch, fotocasa, idealista, pisos_com

FIX = Path(__file__).parent / "fixtures"


def fx(name):
    return (FIX / name).read_text(encoding="utf-8", errors="ignore")


class Page:
    def __init__(self, text, status=200, url="https://example.test/"):
        self.text, self.status_code, self.url, self.headers = text, status, url, {}


class FakeHTTP:
    """Stands in for fetchers.Client: returns the same page for every URL, records URLs."""

    def __init__(self, text):
        self.text, self.urls = text, []

    def get(self, url, **kw):
        self.urls.append(url)
        return Page(self.text, url=url)


# --- pisos.com -----------------------------------------------------------------------

def test_pisos_com_reads_the_30_cards_with_their_geography():
    cards = pisos_com.parse_page(fx("sana-pisos-com.html"))
    assert len(cards) == 30
    assert all(c.get("region") == "Granada" for c in cards)


def test_pisos_com_first_card_field_by_field(cfg):
    row = pisos_com.normalize(pisos_com.parse_page(fx("sana-pisos-com.html"))[0], "sale", cfg)
    assert row["listing_id"] == "pisos_com:67567133218.100500"
    assert row["url"] == "https://www.pisos.com/comprar/apartamento-armilla_san_anton-67567133218_100500/"
    assert row["municipality"] == "Armilla" and row["city"] == "granada"
    assert row["district"] == "San Antón"
    assert (row["price"], row["area_m2"], row["rooms"], row["bathrooms"], row["floor"]) == (158900, 53, 1, 1, "1")
    assert row["property_type"] == "flat"
    assert row["lat"] and row["lon"]


def test_pisos_com_guard_cuts_unlisted_localities(cfg):
    cards = pisos_com.parse_page(fx("sana-pisos-com.html"))
    rows = [pisos_com.normalize(c, "sale", cfg) for c in cards]
    assert "Almuñécar" in {c["locality"] for c, r in zip(cards, rows) if r is None}
    assert all(r["city"] == "granada" for r in rows if r)


def test_pisos_com_capital_suffix_and_no_redundant_district(cfg):
    rows = [pisos_com.normalize(c, "sale", cfg) for c in pisos_com.parse_page(fx("sana-pisos-com.html"))]
    capital = [r for r in rows if r and r["municipality"] == "Granada"]
    assert capital and all(r["district"] != "Granada Capital" for r in capital)


@pytest.mark.parametrize("title,kind", [
    ("Apartamento en calle de Málaga, 6", "flat"), ("Ático en Zaidín", "penthouse"), ("Casa adosada en Armilla", "house"),
    ("Chalet en calle Estación, 4", "detached"), ("Estudio en Centro", "studio"), ("Dúplex en Maracena", "duplex"),
])
def test_pisos_com_type_from_title(title, kind):
    assert pisos_com.property_type(title) == kind


def test_pisos_com_search_url():
    assert (pisos_com.search_url("area_de_granada", "sale", 200000, 1)
            == "https://www.pisos.com/venta/pisos-area_de_granada/hasta-200000/fecharecientedesde-desc/")
    assert pisos_com.search_url("area_de_sevilla", "rent", 650, 3).endswith("/hasta-650/fecharecientedesde-desc/3/")


def test_pisos_com_walks_every_zone_of_every_area(cfg):
    http = FakeHTTP(fx("sana-pisos-com.html"))
    pisos_com.fetch(cfg, "sale", conf=cfg["sources"]["pisos_com"], http=http, quick=True)
    zones = {u.split("/pisos-")[1].split("/")[0] for u in http.urls}
    assert {"area_de_granada", "area_de_sevilla", "el_aljarafe", "la_rinconada"} <= zones


# --- Fotocasa ------------------------------------------------------------------------

def test_fotocasa_page_has_30_listings_and_the_total():
    items, total = fotocasa.search_result(fx("sana-fotocasa-listado.html"))
    assert len(items) == 30
    assert total and total > 30


def test_fotocasa_normalizes_the_whole_page(cfg):
    items, _ = fotocasa.search_result(fx("sana-fotocasa-listado.html"))
    rows = [fotocasa.normalize(x, "sale", cfg) for x in items]
    assert all(r is not None for r in rows)
    for r in rows:
        assert r["municipality"] == "Granada" and r["city"] == "granada"
        assert r["price"] and r["price"] <= 200000
        assert r["url"].startswith("https://www.fotocasa.es/es/comprar/vivienda/")
        assert r["property_type"] != "other"
        assert r["elevator"] in (1, None)        # absence from the feature list proves nothing


def test_fotocasa_region_guard(cfg):
    x = copy.deepcopy(fotocasa.search_result(fx("sana-fotocasa-listado.html"))[0][0])
    x["address"]["province"], x["address"]["municipality"] = "Barcelona", "La Granada"
    assert fotocasa.normalize(x, "sale", cfg) is None


def test_fotocasa_flags_reach_the_filter(cfg):
    x = copy.deepcopy(fotocasa.search_result(fx("sana-fotocasa-listado.html"))[0][0])
    x.update(isBareOwnership=True)
    assert fotocasa.normalize(x, "sale", cfg)["bare_ownership"] is True


def test_fotocasa_locations(cfg):
    locs = dict((loc, a) for a, loc in fotocasa.locations(cfg, {}))
    assert locs["granada-capital"] == "granada" and locs["huetor-vega"] == "granada"
    assert locs["alcala-de-guadaira"] == "sevilla"
    assert ("granada", "custom") in fotocasa.locations(cfg, {"slugs": {"Granada": "custom"}})


def test_fotocasa_url_carries_the_filters(cfg):
    u = fotocasa.search_url("armilla", "rent", cfg, 2)
    assert u.startswith("https://www.fotocasa.es/es/alquiler/viviendas/armilla/todas-las-zonas/l/2?")
    assert "maxPrice=650" in u and "minRooms=2" in u and "minSurface=70" in u and "sortType=publicationDate" in u


def test_fotocasa_paging_stops_at_the_cap(cfg):
    http = FakeHTTP(fx("sana-fotocasa-listado.html"))
    out, seen = Batch(), set()
    fotocasa._walk(http, "granada-capital", "sale", cfg, 2, out, seen, "granada:sale")
    assert len(http.urls) == 2                 # the fixture says there are more than 30
    assert "granada:sale" in out.incomplete
    assert len(out) == 30                      # page two repeats ids: no duplicates


# --- Idealista -----------------------------------------------------------------------

def test_idealista_alerts_makes_no_requests(cfg, monkeypatch):
    monkeypatch.setattr(idealista.requests, "post", lambda *a, **k: pytest.fail("must not call"))
    out = idealista.fetch(cfg, "sale", conf={"backend": "alerts"})
    assert out == [] and out.notes


def test_idealista_official_respects_the_quota(cfg, tmp_path, monkeypatch):
    import db
    con = db.connect(tmp_path / "r.db")
    monkeypatch.setenv("IDEALISTA_API_KEY", "k")
    monkeypatch.setenv("IDEALISTA_API_SECRET", "s")
    monkeypatch.setattr(idealista, "token", lambda k, s: "tok")

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"elementList": [{"propertyCode": "1", "price": 150000, "size": 80, "rooms": 2,
                                     "province": "Granada", "municipality": "Armilla", "propertyType": "flat",
                                     "hasLift": True, "url": "https://www.idealista.com/inmueble/1/"}]}

    posts = []
    monkeypatch.setattr(idealista.requests, "post", lambda *a, **k: posts.append(a) or R())
    conf = {**cfg["sources"]["idealista"], "backend": "official", "monthly_quota": 1}
    out = idealista.fetch(cfg, "sale", conf=conf, con=con)
    assert len(posts) == 1                     # two centers, quota of one call
    assert out[0]["listing_id"] == "idealista:1" and out[0]["elevator"] == 1
    assert any("quota" in n for n in out.notes)


# --- the contract for plugins ----------------------------------------------------------

def test_a_plugin_source_from_a_workspace_file(cfg, tmp_path, monkeypatch):
    import sources
    ws = tmp_path / "ws"
    (ws / "plugins").mkdir(parents=True)
    (ws / "plugins" / "my_portal.py").write_text(
        "def fetch(cfg, operation, *, conf, http, **kw):\n"
        "    return [{'listing_id': 'my_portal:1', 'source': 'my_portal', 'operation': operation,\n"
        "             'city': 'granada', 'municipality': 'Granada', 'price': conf['price']}]\n")
    monkeypatch.setenv("RADAR_WORKSPACE", str(ws))
    c = {**cfg, "sources": {"my_portal": {"path": "plugins/my_portal.py", "price": 1234}}}
    mod = sources.load("my_portal", c)
    rows = sources.as_batch(mod.fetch(c, "sale", conf=c["sources"]["my_portal"], http=None), {"granada:sale"})
    assert rows[0]["price"] == 1234
    assert rows.incomplete == {"granada:sale"}          # a plain list is never a full walk


def test_enabled_sources_follow_the_config():
    import sources
    assert sources.enabled({"sources": {"a": {}, "b": {"enabled": False}, "c": None}}) == ["a", "c"]
