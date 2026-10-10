"""Daily e-mail and fast alerts: when to send, what gets marked and when."""
import json
from pathlib import Path

import pytest

import db
import ingest
import report
import run
import watch

TODAY = "2026-10-10"


@pytest.fixture
def con(tmp_path):
    return db.connect(tmp_path / "radar.db")


def row(i, **kw):
    base = {"listing_id": f"fotocasa:{i}", "source": "fotocasa", "url": f"https://www.fotocasa.es/x/{i}/d",
            "operation": "sale", "title": "Piso en Zaidín", "city": "granada", "municipality": "Granada",
            "district": "Zaidín", "price": 150000.0, "area_m2": 90.0, "rooms": 3, "property_type": "flat",
            "elevator": 1, "description": "Exterior", "photos": "[]"}
    return {**base, **kw}


def test_nothing_relevant_means_no_e_mail(con, cfg, tmp_path):
    (tmp_path / "evals.json").write_text(json.dumps([{"listing_id": "fotocasa:1", "fit": "Low"}]))
    assert report.daily(con, cfg, tmp_path, TODAY) == 3


def test_matching_listings_compose_and_are_marked_only_after_sending(con, cfg, tmp_path):
    ingest.upsert(con, row("1"), cfg, TODAY, "daily")
    ingest.upsert(con, row("2", price=120000.0, rooms=2), cfg, TODAY, "daily")
    (tmp_path / "stats.json").write_text(json.dumps({"sources": {"fotocasa": {"items": 2, "errors": [], "notes": []}},
                                                     "new_passing": 2, "pending_evaluation": 2}))
    (tmp_path / "evals.json").write_text(json.dumps([
        {"listing_id": "fotocasa:1", "fit": "High", "priority": "P1", "zone_assessment": "Metro 300 m away",
         "value_assessment": "Below the zone", "red_flags": ["high fees"], "summary": "3-bed flat"},
        {"listing_id": "fotocasa:2", "fit": "Reject", "priority": "P3", "red_flags": [], "summary": "A room"},
    ]))
    assert report.daily(con, cfg, tmp_path, TODAY) == 0
    html = (tmp_path / "email.html").read_text()
    assert "Metro 300 m away" in html and "high fees" in html and "A room" not in html
    assert (tmp_path / "email_subject.txt").read_text() == "Housing radar 2026-10-10: 1 matching (1 strong)"
    r = con.execute("SELECT status, notified_at FROM listings WHERE listing_id='fotocasa:1'").fetchone()
    assert (r["status"], r["notified_at"]) == ("Evaluated", None)
    report.mark(con, tmp_path, TODAY)
    r = con.execute("SELECT status, notified_at FROM listings WHERE listing_id='fotocasa:1'").fetchone()
    assert (r["status"], r["notified_at"]) == ("Notified", TODAY)


def test_the_e_mail_follows_the_configured_language(con, cfg, tmp_path):
    ingest.upsert(con, row("1"), cfg, TODAY, "daily")
    (tmp_path / "evals.json").write_text(json.dumps([{"listing_id": "fotocasa:1", "fit": "High", "priority": "P1"}]))
    assert report.daily(con, {**cfg, "language": "es"}, tmp_path, TODAY) == 0
    assert (tmp_path / "email_subject.txt").read_text().startswith("Radar de vivienda 2026-10-10: 1 con encaje")
    assert "150.000 €" in (tmp_path / "email.html").read_text()


def test_a_price_drop_on_a_matching_listing_is_announced(con, cfg, tmp_path):
    ingest.upsert(con, row("1"), cfg, "2026-10-09", "daily")
    con.execute("UPDATE listings SET fit='Medium', status='Notified' WHERE listing_id='fotocasa:1'")
    ingest.upsert(con, row("1", price=139000.0), cfg, TODAY, "daily")
    assert report.daily(con, cfg, tmp_path, TODAY) == 0
    assert "€139,000" in (tmp_path / "email.html").read_text()


def test_unreadable_evals_do_not_break_it(con, cfg, tmp_path):
    (tmp_path / "evals.json").write_text("not json")
    assert report.daily(con, cfg, tmp_path, TODAY) == 3


# --- fast alerts -----------------------------------------------------------------------

def fake_sweep(rows):
    def sweep(con, cfg, today, names, *, quick=False, max_pages=None, found_by="daily"):
        ids = []
        for r in rows:
            if ingest.upsert(con, r, cfg, today, found_by) == "new":
                ids.append(r["listing_id"])
        return {}, ids, []
    return sweep


def test_no_alerts_before_the_first_daily_sweep(con, cfg, monkeypatch):
    monkeypatch.setattr(run, "sweep", fake_sweep([row("1")]))
    assert watch.scan(con, cfg, TODAY) == 3


def test_alerts_announce_what_is_new_once(con, cfg, monkeypatch):
    con.execute("INSERT INTO runs VALUES (?, 'fotocasa:sale', 1, 10, NULL, NULL)", (TODAY,))
    ingest.upsert(con, row("old"), cfg, TODAY, "daily")          # seen by the daily: not announced here
    monkeypatch.setattr(run, "sweep", fake_sweep([row("old"), row("new", price=130000.0, rooms=4),
                                                  row("dear", price=260000.0)]))
    assert watch.scan(con, cfg, TODAY) == 0
    out = Path(watch.workspace.out_dir(TODAY))
    prefix = next(out.glob("watch_*_ids.json")).as_posix()[:-len("ids.json")]
    assert json.loads(Path(prefix + "ids.json").read_text()) == ["fotocasa:new"]
    assert Path(prefix + "email_subject.txt").read_text() == "Housing: 1 new — Granada 1"
    watch.mark(con, prefix)
    assert watch.scan(con, cfg, TODAY) == 3                         # already announced


def test_an_unsent_alert_is_retried(con, cfg, monkeypatch):
    con.execute("INSERT INTO runs VALUES (?, 'fotocasa:sale', 1, 10, NULL, NULL)", (TODAY,))
    monkeypatch.setattr(run, "sweep", fake_sweep([row("new")]))
    assert watch.scan(con, cfg, TODAY) == 0
    monkeypatch.setattr(run, "sweep", fake_sweep([]))               # no mark: sending failed
    assert watch.scan(con, cfg, TODAY) == 0


def test_alert_threshold_per_operation(con, cfg):
    for r in (row("low_sale", price=199000.0), row("rent", operation="rent", price=600.0)):
        ingest.upsert(con, r, cfg, TODAY, "watch")
    con.execute("UPDATE listings SET prescore = 0.5")
    ids = {r["listing_id"] for r in watch.pending(con, TODAY, {"sale": 2.0, "rent": None})}
    assert ids == {"fotocasa:rent"}
