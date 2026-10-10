"""Daily sweep: download, hard filter, duplicates, withdrawals and what gets evaluated today.
No LLM.

Usage: python radar/run.py [--workspace DIR] [--date YYYY-MM-DD] [--max-eval N]
                           [--max-pages N] [--sources a,b] [--skip-sweep]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "radar"))
import db  # noqa: E402
import fetchers  # noqa: E402
import filters  # noqa: E402
import ingest  # noqa: E402
import sources  # noqa: E402
import workspace  # noqa: E402

OPERATIONS = ("sale", "rent")


def operations(cfg) -> list[str]:
    return [op for op in OPERATIONS if op in (cfg.get("budgets") or {})]


def sweep(con, cfg, today, names, *, quick=False, max_pages=None, found_by="daily"):
    """Download and store. Returns (stats per source, new ids, withdrawn ids)."""
    stats, new_ids, withdrawn = {}, [], []
    all_keys = {f"{a}:{op}" for a in filters.area_names(cfg) for op in operations(cfg)}
    for name in names:
        st = stats.setdefault(name, {"items": 0, "new": 0, "price": 0, "dropped": 0, "errors": [], "notes": []})
        conf = sources.configured(cfg).get(name) or {}
        try:
            mod = sources.load(name, cfg)
            http = fetchers.client_for(conf)
        except Exception as e:  # noqa: BLE001 - a broken plugin must not stop the others
            st["errors"].append(f"load: {type(e).__name__}: {e}"[:300])
            continue
        seen: set[str] = set()
        complete: set[str] = set()
        for op in operations(cfg):
            challenged = error = None
            batch = None
            try:
                result = mod.fetch(cfg, op, conf=conf, http=http, quick=quick, max_pages=max_pages, con=con)
                batch = sources.as_batch(result, {k for k in all_keys if k.endswith(f":{op}")})
            except fetchers.Challenged as e:
                challenged = e.label
            except Exception as e:  # noqa: BLE001 - one source down does not stop the sweep
                error = f"{type(e).__name__}: {e}"[:300]
            con.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?)",
                        (today, f"{name}:{op}" + (":quick" if quick else ""), int(batch is not None),
                         len(batch or []), challenged, error))
            if batch is None:
                st["errors"].append(f"{op}: {challenged or error}")
                continue
            st["dropped"] += batch.dropped
            st["notes"] += batch.notes
            for row in batch:
                seen.add(row["listing_id"])
                outcome = ingest.upsert(con, row, cfg, today, found_by)
                st["items"] += 1
                if outcome == "new":
                    st["new"] += 1
                    new_ids.append(row["listing_id"])
                elif outcome == "price":
                    st["price"] += 1
            complete |= {k for k in all_keys if k.endswith(f":{op}")} - batch.incomplete
            con.commit()
        if not quick and getattr(mod, "FULL_WALK", False) and complete:
            withdrawn += ingest.mark_missing(con, name, complete, seen, today)
            con.commit()
    return stats, new_ids, withdrawn


def enrich(con, cfg, row, today) -> dict | None:
    """Full listing for what is about to be evaluated. None if it no longer passes."""
    name = row["source"]
    conf = sources.configured(cfg).get(name) or {}
    try:
        mod = sources.load(name, cfg)
    except Exception:  # noqa: BLE001 - source removed from the config since it was stored
        return dict(row)
    if not hasattr(mod, "detail") or not row["url"]:
        return dict(row)
    try:
        d = mod.detail(row["url"], http=fetchers.client_for(conf))
    except Exception:  # noqa: BLE001 - without the detail page, evaluate with the excerpt
        return dict(row)
    if d.get("gone"):
        con.execute("UPDATE listings SET status='Withdrawn', withdrawn_on=? WHERE listing_id=?", (today, row["listing_id"]))
        return None
    merged = {**dict(row), **{k: v for k, v in d.items() if k in ("description", "elevator", "condition") and v is not None}}
    again = filters.hard_filter(merged, cfg)
    con.execute("UPDATE listings SET description=?, elevator=?, condition=?, passes=?, filter_reason=?, to_confirm=?, "
                "status=? WHERE listing_id=?",
                (merged["description"], merged.get("elevator"), merged.get("condition"), again["passes"],
                 again["filter_reason"], again["to_confirm"], "New" if again["passes"] else "Filtered",
                 row["listing_id"]))
    if not again["passes"]:
        return None
    merged.update(features=d.get("features"), to_confirm=again["to_confirm"])
    return merged


def queue(con) -> list:
    """Pending listings in turns per (area, operation), each group by its pre-score.
    Without turns, ~2,000 pending sales left no room for ~30 rentals."""
    groups: dict = {}
    for r in con.execute("SELECT * FROM listings WHERE status='New' AND passes=1 "
                         "ORDER BY prescore DESC, first_seen DESC").fetchall():
        groups.setdefault((r["city"], r["operation"]), []).append(r)
    lists = [groups[k] for k in sorted(groups)]
    out = []
    for i in range(max((len(g) for g in lists), default=0)):
        out += [g[i] for g in lists if i < len(g)]
    return out


def pick(con, cfg, today, max_eval) -> list[dict]:
    out = []
    for r in queue(con):
        if len(out) >= max_eval:
            break
        row = enrich(con, cfg, r, today)
        con.commit()
        if row is None:
            continue
        st = ingest.zone_stats(con, row["operation"], row["municipality"], row["district"], today,
                               filters.kind(row["property_type"]))
        vz = ingest.vs_zone(row, st)
        hist = [dict(h) for h in con.execute("SELECT date, price FROM price_history WHERE listing_id=? ORDER BY date",
                                             (row["listing_id"],))]
        out.append({
            "listing_id": row["listing_id"], "source": row["source"], "url": row["url"],
            "operation": row["operation"], "area": row["city"], "municipality": row["municipality"],
            "district": row["district"], "address": row["address"], "title": row["title"],
            "price": row["price"], "currency": cfg.get("currency", "EUR"), "area_m2": row["area_m2"],
            "rooms": row["rooms"], "bathrooms": row["bathrooms"], "floor": row["floor"],
            "property_type": row["property_type"], "elevator": row["elevator"], "condition": row["condition"],
            "posted": row["posted"], "first_seen": row["first_seen"],
            "price_per_m2": filters.price_per_m2(row), "zone": st, "vs_zone_pct": vz,
            "price_anomaly": (vz or 0) <= ingest.ANOMALY_PCT,
            "price_history": hist, "to_confirm": row["to_confirm"], "prescore": row["prescore"],
            "features": row.get("features"), "description": (row["description"] or "")[:4000],
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace")
    ap.add_argument("--date")
    ap.add_argument("--max-eval", type=int)
    ap.add_argument("--max-pages", type=int, help="page cap per search (testing)")
    ap.add_argument("--sources", help="comma-separated; default: every enabled source")
    ap.add_argument("--skip-sweep", action="store_true", help="no downloads: only score and pick")
    args = ap.parse_args()
    workspace.use(args.workspace)
    cfg = workspace.load_config()
    today = args.date or workspace.today(cfg)
    con = db.connect()
    names = args.sources.split(",") if args.sources else sources.enabled(cfg)
    out = workspace.out_dir(today)

    if args.skip_sweep:
        stats, new_ids, withdrawn = {}, [], []
        if (out / "stats.json").exists():       # keep what today's sweep said, for the e-mail
            old = json.loads((out / "stats.json").read_text())
            stats, withdrawn = old.get("sources", {}), old.get("withdrawn", [])
    else:
        stats, new_ids, withdrawn = sweep(con, cfg, today, names, max_pages=args.max_pages)
    checked, freed = ingest.recheck_duplicates(con)
    ingest.rescore(con, today)
    con.commit()
    max_eval = args.max_eval or (cfg.get("evaluation") or {}).get("max_per_day", 25)
    to_eval = pick(con, cfg, today, max_eval)

    (out / "to_evaluate.json").write_text(json.dumps(to_eval, indent=1, ensure_ascii=False))
    summary = {
        "date": today, "sources": stats, "new_total": len(new_ids),
        "new_passing": con.execute("SELECT count(*) FROM listings WHERE first_seen=? AND passes=1 AND status='New'",
                                   (today,)).fetchone()[0],
        "withdrawn": withdrawn,
        "duplicates": {"checked": checked, "released": freed},
        "pending_evaluation": con.execute("SELECT count(*) FROM listings WHERE status='New' AND passes=1").fetchone()[0],
        "evaluating_today": len(to_eval),
    }
    (out / "stats.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in summary.items() if k != "withdrawn"} | {"withdrawn": len(withdrawn)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
