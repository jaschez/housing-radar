"""Fast cadence (e.g. every 30 minutes during the day): the first page of each search,
newest first, hard filter, and an e-mail with what is new and matches. No evaluation and
no LLM: that comes in the daily e-mail.

Usage:
  python radar/watch.py scan [--date D] [--workspace W]   -> prints the e-mail prefix and exits 0, or exits 3
  python radar/watch.py mark <prefix> [--workspace W]     -> mark that e-mail's listings as notified

What gets announced: listings this cadence saw first (found_by = 'watch'), that pass the
filter, are not duplicates and were not announced yet. What the daily sweep finds is not
announced here: it reaches the daily e-mail if the evaluation likes it. Before the first
complete daily sweep nothing is announced: without history everything would look new.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "radar"))
import db  # noqa: E402
import i18n  # noqa: E402
import ingest  # noqa: E402
import report  # noqa: E402
import run  # noqa: E402
import sources  # noqa: E402
import workspace  # noqa: E402


def seeded(con) -> bool:
    return con.execute("SELECT 1 FROM runs WHERE ok = 1 AND source NOT LIKE '%:quick' LIMIT 1").fetchone() is not None


def pending(con, today: str, min_prescore=None) -> list[dict]:
    """What is due. `min_prescore` (watch.min_prescore, per operation) leaves out low scores:
    they are not lost, they reach the daily e-mail if the evaluation likes them."""
    since = (dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat()
    floors = min_prescore if isinstance(min_prescore, dict) else {"sale": min_prescore, "rent": min_prescore}
    rows = con.execute(
        """SELECT * FROM listings WHERE found_by = 'watch' AND status = 'New' AND passes = 1
             AND watch_notified_at IS NULL AND first_seen >= ?
           ORDER BY prescore DESC, first_seen DESC""", (since,)).fetchall()
    return [dict(r) for r in rows
            if floors.get(r["operation"]) is None or (r["prescore"] or 0) >= floors[r["operation"]]]


def scan(con, cfg, today: str) -> int:
    if not seeded(con):
        print("no daily sweep yet: nothing is announced")
        return 3
    _, new_ids, _ = run.sweep(con, cfg, today, sources.enabled(cfg), quick=True, found_by="watch")
    ingest.rescore(con, today, new_ids)
    con.commit()
    wcfg = cfg.get("watch") or {}
    rows = pending(con, today, wcfg.get("min_prescore"))
    if not rows:
        return 3
    t = i18n.T(cfg)
    rnd = report.Renderer(con, cfg, today)
    cap = wcfg.get("max_items_per_mail", 15)
    shown, rest = rows[:cap], len(rows) - cap
    out = workspace.out_dir(today)
    prefix = f"watch_{dt.datetime.now().strftime('%H%M')}_"
    by_place: dict[str, int] = {}
    for r in rows:
        by_place[r["municipality"]] = by_place.get(r["municipality"], 0) + 1
    subject = t("watch.subject", n=len(rows), where=", ".join(f"{k} {v}" for k, v in sorted(by_place.items())))
    more = f"<p>{report.esc(t('watch.more', n=rest))}</p>" if rest > 0 else ""
    report.write_mail(out, prefix, t, subject[:150],
                      f"<p>{report.esc(t('watch.intro'))}</p>" + rnd.html(shown, evaluated=False) + more,
                      t("watch.intro") + "\n\n" + rnd.text(shown, evaluated=False)
                      + (f"\n\n{t('watch.more', n=rest)}" if rest > 0 else ""))
    (out / f"{prefix}ids.json").write_text(json.dumps([r["listing_id"] for r in rows]))
    print(out / prefix)
    return 0


def mark(con, prefix: str) -> int:
    ids = json.loads(Path(f"{prefix}ids.json").read_text())
    if ids:
        con.execute(f"UPDATE listings SET watch_notified_at = ? WHERE listing_id IN ({','.join('?' * len(ids))})",
                    (dt.datetime.now().isoformat(timespec="minutes"), *ids))
        con.commit()
    print(f"marked {len(ids)}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["scan", "mark"])
    ap.add_argument("prefix", nargs="?")
    ap.add_argument("--date")
    ap.add_argument("--workspace")
    a = ap.parse_args()
    workspace.use(a.workspace)
    cfg = workspace.load_config()
    con = db.connect()
    if a.mode == "scan":
        sys.exit(scan(con, cfg, a.date or workspace.today(cfg)))
    sys.exit(mark(con, a.prefix))


if __name__ == "__main__":
    main()
