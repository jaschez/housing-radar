"""The examples are documentation: they must keep working."""
import json
from pathlib import Path

import yaml

import filters
import plugins
import sources

ROOT = Path(__file__).resolve().parents[1]


def test_example_config_is_complete():
    cfg = yaml.safe_load((ROOT / "examples/workspace/config.yaml").read_text(encoding="utf-8"))
    for key in ("language", "notify", "evaluation", "budgets", "minimums", "areas", "exclusions", "sources", "watch"):
        assert key in cfg, key
    assert set(cfg["excluded_property_types"]) <= filters.PROPERTY_TYPES
    names = set(filters.area_names(cfg))
    assert set(cfg["sources"]["pisos_com"]["zones"]) <= names
    assert set(cfg["sources"]["idealista"]["centers"]) <= names
    # pisos.com writes "València", Fotocasa "Valencia": both must land in the same area.
    assert filters.zone_of("València", "València", cfg)[0] == filters.zone_of("Valencia", "Valencia", cfg)[0] == "valencia"


def test_example_source_plugin_follows_the_contract(cfg):
    mod = plugins.load({"path": str(ROOT / "examples/plugins/example_source.py")}, "sources")

    class R:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    pages = [
        {"results": [{"id": 1, "region": "Granada", "town": "Armilla", "price": 150000, "size_m2": 80, "bedrooms": 3,
                      "kind": "apartment", "lift": True}], "next_page": 2},
        {"results": [{"id": 2, "region": "Barcelona", "town": "La Granada", "price": 1}], "next_page": None},
    ]

    class HTTP:
        def get(self, url, params=None, **kw):
            return R(pages.pop(0) if pages else {"results": []})

    c = {**cfg, "areas": [cfg["areas"][0]]}
    out = sources.as_batch(mod.fetch(c, "sale", conf={"base_url": "https://x.test"}, http=HTTP()), {"granada:sale"})
    assert [r["listing_id"] for r in out] == ["my_portal:1"]
    assert out[0]["property_type"] == "flat" and out[0]["elevator"] == 1
    assert out.dropped == 1 and out.incomplete == set()      # full walk reached the end
    json.dumps(list(out))
