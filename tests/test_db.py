"""The store: never deletes rows, and the schema is applied on connect."""
import sqlite3

import db


def test_connect_creates_the_four_tables(tmp_path):
    con = db.connect(tmp_path / "radar.db")
    tables = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"listings", "price_history", "runs", "quota"} <= tables


def test_connect_is_idempotent(tmp_path):
    p = tmp_path / "radar.db"
    con = db.connect(p)
    con.execute("INSERT INTO listings (listing_id, source, url) VALUES ('x', 'test', 'http://e')")
    con.commit()
    con.close()
    assert db.connect(p).execute("SELECT count(*) FROM listings").fetchone()[0] == 1


def test_default_path_is_inside_the_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("RADAR_WORKSPACE", str(tmp_path / "ws"))
    db.connect()
    assert (tmp_path / "ws" / "data" / "radar.db").exists()


def test_two_notification_markers_are_different_columns(tmp_path):
    # The fast cadence and the daily e-mail announce different things: the fast one says
    # "a new listing matches", the daily one brings the zone and value assessment. With a
    # shared marker, a listing announced by the fast cadence would never reach the daily
    # e-mail and its assessment would be lost.
    con = db.connect(tmp_path / "radar.db")
    cols = {r["name"] for r in con.execute("PRAGMA table_info(listings)")}
    assert {"notified_at", "watch_notified_at"} <= cols


def test_added_columns_reach_an_old_database(tmp_path):
    p = tmp_path / "old.db"
    old = sqlite3.connect(p)
    old.executescript(db.SCHEMA)               # first schema version: without the added columns
    old.execute("INSERT INTO listings (listing_id, source) VALUES ('a', 'x')")
    old.commit()
    old.close()
    con = db.connect(p)
    cols = {r["name"] for r in con.execute("PRAGMA table_info(listings)")}
    assert {"title", "found_by", "lat", "lon"} <= cols
    assert con.execute("SELECT count(*) FROM listings").fetchone()[0] == 1
