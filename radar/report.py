"""Store Claude's evaluations and compose the daily e-mail.

Usage:
  python radar/report.py daily [--date D] [--workspace W]   -> exit 0 if there is something to send, 3 if not
  python radar/report.py mark  [--date D] [--workspace W]   -> mark today's e-mail as sent

Marking is separate and comes AFTER sending: if the e-mail fails, today's listings stay
pending and are retried on the next run of the day instead of counting as notified.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "radar"))
import db  # noqa: E402
import filters  # noqa: E402
import i18n  # noqa: E402
import ingest  # noqa: E402
import workspace  # noqa: E402

FIT_ORDER = {"High": 0, "Medium": 1, "Low": 2, "Reject": 3}


def esc(s):
    return html.escape(str(s if s is not None else ""))


def ingest_evals(con, out: Path, today: str) -> list[dict]:
    f = out / "evals.json"
    if not f.exists():
        return []
    try:
        evals = json.loads(f.read_text())
    except ValueError:
        print("evals.json unreadable: ignored")
        return []
    for e in evals:
        if not isinstance(e, dict) or not e.get("listing_id"):
            continue
        flags = e.get("red_flags")
        con.execute(
            """UPDATE listings SET fit=?, priority=?, zone_assessment=?, value_assessment=?, red_flags=?, summary=?,
               evaluated_at=?, status=CASE WHEN status='New' THEN 'Evaluated' ELSE status END
               WHERE listing_id=?""",
            (e.get("fit"), e.get("priority"), e.get("zone_assessment"), e.get("value_assessment"),
             json.dumps(flags, ensure_ascii=False) if isinstance(flags, list) else flags, e.get("summary"),
             today, e["listing_id"]))
    con.commit()
    return evals


def rows_by_id(con, ids) -> list[dict]:
    if not ids:
        return []
    q = f"SELECT * FROM listings WHERE listing_id IN ({','.join('?' * len(ids))})"
    return [dict(r) for r in con.execute(q, list(ids)).fetchall()]


class Renderer:
    def __init__(self, con, cfg: dict, today: str):
        self.con, self.today, self.t = con, today, i18n.T(cfg)
        self._zones: dict = {}

    def zone(self, r) -> dict:
        key = (r["operation"], r["municipality"], r["district"], self.today, filters.kind(r["property_type"]))
        if key not in self._zones:
            self._zones[key] = ingest.zone_stats(self.con, *key)
        return self._zones[key]

    def facts(self, r) -> str:
        """One line of hard data: price, price per m2 against the zone, area, rooms..."""
        t = self.t
        st = self.zone(r)
        vz = ingest.vs_zone(r, st)
        ppm = filters.price_per_m2(r)
        bits = [t.money(r["price"], r["operation"])]
        if ppm:
            num = t.number(ppm, 1 if r["operation"] == "rent" else 0)
            sym = i18n.CURRENCY_SYMBOLS.get(t.currency, t.currency)
            vs = (" " + t("vs_zone", pct=vz, kind=t(f"kind.{st['kind']}"), zone=st["zone"])) if vz is not None else ""
            bits.append(f"{num} {sym}/m²{vs}")
        if r["area_m2"]:
            bits.append(f"{r['area_m2']:.0f} m²")
        if r["rooms"]:
            bits.append(t("rooms", n=r["rooms"]))
        if r["bathrooms"]:
            bits.append(t("baths", n=r["bathrooms"]))
        if r["floor"]:
            bits.append(t("floor", f=r["floor"]))
        if r["property_type"] not in filters.HOUSE_TYPES:     # in a house, an elevator is not a question
            bits.append(t({1: "elevator.1", 0: "elevator.0"}.get(r["elevator"], "elevator.none")))
        if vz is not None and vz <= ingest.ANOMALY_PCT:
            bits.append(t("anomaly"))
        return " · ".join(bits)

    def where(self, r) -> str:
        t = self.t
        zone = f"{r['district']}, {r['municipality']}" if r.get("district") else r["municipality"]
        return f"{t('type.' + (r['property_type'] or 'other'))} {t('op.' + r['operation'])} — {zone}"

    def to_confirm(self, r) -> str:
        return ", ".join(self.t(f"confirm.{k.strip()}") for k in (r.get("to_confirm") or "").split(",") if k.strip())

    @staticmethod
    def flags(r) -> str:
        f = r.get("red_flags")
        try:
            f = json.loads(f) if f and f.startswith("[") else f
        except ValueError:
            pass
        return "; ".join(f) if isinstance(f, list) else (f or "")

    def html(self, rows, evaluated=True) -> str:
        t, parts = self.t, []
        for r in rows:
            head = f"[{esc(r['fit'])} · {esc(r['priority'])}] " if evaluated and r.get("fit") else ""
            parts.append(
                f"<li style='margin-bottom:12px'><b>{head}<a href='{esc(r['url'])}'>{esc(self.where(r))}</a></b> "
                f"<span style='color:#777'>({esc(r['source'])})</span><br>{esc(self.facts(r))}"
                + (f"<br>{esc(r['summary'])}" if evaluated and r.get("summary") else "")
                + (f"<br><i>{t('zone')}:</i> {esc(r['zone_assessment'])}" if evaluated and r.get("zone_assessment") else "")
                + (f"<br><i>{t('value')}:</i> {esc(r['value_assessment'])}" if evaluated and r.get("value_assessment") else "")
                + (f"<br><i>{t('flags')}:</i> {esc(self.flags(r))}" if evaluated and self.flags(r) else "")
                + (f"<br><i>{t('to_confirm')}:</i> {esc(self.to_confirm(r))}" if r.get("to_confirm") else "")
                + "</li>")
        return "<ul>" + "".join(parts) + "</ul>"

    def text(self, rows, evaluated=True) -> str:
        t, out = self.t, []
        for r in rows:
            head = f"[{r['fit']} · {r['priority']}] " if evaluated and r.get("fit") else ""
            lines = [f"{head}{self.where(r)} ({r['source']})", f"  {r['url']}", f"  {self.facts(r)}"]
            if evaluated:
                lines += [f"  {r['summary']}" if r.get("summary") else "",
                          f"  {t('zone')}: {r['zone_assessment']}" if r.get("zone_assessment") else "",
                          f"  {t('value')}: {r['value_assessment']}" if r.get("value_assessment") else "",
                          f"  {t('flags')}: {self.flags(r)}" if self.flags(r) else ""]
            if r.get("to_confirm"):
                lines.append(f"  {t('to_confirm')}: {self.to_confirm(r)}")
            out.append("\n".join(x for x in lines if x))
        return "\n\n".join(out)


def write_mail(out: Path, prefix: str, t: i18n.T, subject: str, html_body: str, text_body: str):
    footer = f"<hr><p style='color:#777;font-size:12px'>{esc(t('footer'))}</p>"
    (out / f"{prefix}email_subject.txt").write_text(subject)
    (out / f"{prefix}email.html").write_text(
        f"<div style='font-family:sans-serif;font-size:14px'>{html_body}{footer}</div>")
    (out / f"{prefix}email.txt").write_text(text_body)


def price_drops(con, today) -> list[dict]:
    """Today's drops on listings that matched (or were already notified) and are still online."""
    out = []
    for r in con.execute(
            """SELECT l.*, (SELECT price FROM price_history p WHERE p.listing_id = l.listing_id AND p.date < ?
                            ORDER BY p.date DESC LIMIT 1) AS prev_price
               FROM listings l WHERE l.status != 'Withdrawn' AND l.fit IN ('High', 'Medium')
                 AND EXISTS (SELECT 1 FROM price_history p WHERE p.listing_id = l.listing_id AND p.date = ?)""",
            (today, today)).fetchall():
        if r["prev_price"] and r["price"] and r["price"] < r["prev_price"]:
            out.append(dict(r))
    return out


def daily(con, cfg: dict, out: Path, today: str) -> int:
    rnd = Renderer(con, cfg, today)
    t = rnd.t
    evals = ingest_evals(con, out, today)
    stats = json.loads((out / "stats.json").read_text()) if (out / "stats.json").exists() else {}
    good_ids = [e["listing_id"] for e in evals if isinstance(e, dict) and e.get("fit") in ("High", "Medium")]
    rows = sorted(rows_by_id(con, good_ids),
                  key=lambda r: (FIT_ORDER.get(r.get("fit"), 9), r.get("priority") or "P9", r["price"] or 0))
    drops = price_drops(con, today)
    gone = [dict(r) for r in con.execute(
        "SELECT * FROM listings WHERE withdrawn_on = ? AND fit IN ('High', 'Medium')", (today,)).fetchall()]
    (out / "daily_ids.json").write_text(json.dumps(good_ids))
    if not rows and not drops and not gone:
        return 3

    src = stats.get("sources", {})
    seen = ", ".join(f"{k} {v['items']}" for k, v in src.items() if v.get("items"))
    errors = [f"{k} ({'; '.join(v['errors'])})" for k, v in src.items() if v.get("errors")]
    notes = sorted({n for v in src.values() for n in v.get("notes", [])})
    n_high = sum(1 for r in rows if r["fit"] == "High")
    subject = (t("daily.subject", day=today, n=len(rows)) + (t("daily.subject_high", n=n_high) if n_high else "")
               + (t("daily.subject_drops", n=len(drops)) if drops else "")
               + (t("daily.subject_gone", n=len(gone)) if gone else ""))
    intro = t("daily.intro", seen=seen or t("daily.no_data"), new=stats.get("new_passing", "?"),
              evaluated=len(evals), queue=max(stats.get("pending_evaluation", 0) - len(evals), 0))
    h, txt = [f"<p>{esc(intro)}</p>"], [intro]
    if rows:
        h.append(f"<h3>{esc(t('daily.matching'))}</h3>" + rnd.html(rows))
        txt.append(t("daily.matching").upper() + "\n\n" + rnd.text(rows))
    if drops:
        h.append(f"<h3>{esc(t('daily.drops'))}</h3><ul>" + "".join(
            f"<li><a href='{esc(r['url'])}'>{esc(rnd.where(r))}</a>: {esc(t.money(r['prev_price'], r['operation']))} → "
            f"<b>{esc(t.money(r['price'], r['operation']))}</b></li>" for r in drops) + "</ul>")
        txt.append(t("daily.drops").upper() + "\n" + "\n".join(
            f"- {rnd.where(r)}: {t.money(r['prev_price'], r['operation'])} -> {t.money(r['price'], r['operation'])} {r['url']}"
            for r in drops))
    if gone:
        h.append(f"<h3>{esc(t('daily.gone'))}</h3><ul>" + "".join(
            f"<li>{esc(rnd.where(r))} — {esc(t.money(r['price'], r['operation']))}</li>" for r in gone) + "</ul>")
        txt.append(t("daily.gone").upper() + "\n" + "\n".join(
            f"- {rnd.where(r)} — {t.money(r['price'], r['operation'])}" for r in gone))
    if errors:
        h.append(f"<p style='color:#a00'>{esc(t('daily.errors'))}: " + esc("; ".join(errors)) + "</p>")
        txt.append(f"{t('daily.errors')}: " + "; ".join(errors))
    if notes:
        h.append("<p style='color:#777'>" + "<br>".join(esc(n) for n in notes) + "</p>")
    write_mail(out, "", t, subject, "".join(h), "\n\n".join(txt))
    return 0


def mark(con, out: Path, today: str) -> int:
    f = out / "daily_ids.json"
    ids = json.loads(f.read_text()) if f.exists() else []
    if ids:
        con.execute(f"UPDATE listings SET notified_at = ?, status = CASE WHEN status = 'Evaluated' THEN 'Notified' "
                    f"ELSE status END WHERE listing_id IN ({','.join('?' * len(ids))})", (today, *ids))
        con.commit()
    print(f"marked {len(ids)}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["daily", "mark"])
    ap.add_argument("--date")
    ap.add_argument("--workspace")
    a = ap.parse_args()
    workspace.use(a.workspace)
    cfg = workspace.load_config()
    today = a.date or workspace.today(cfg)
    out = workspace.out_dir(today)
    con = db.connect()
    sys.exit(daily(con, cfg, out, today) if a.mode == "daily" else mark(con, out, today))


if __name__ == "__main__":
    main()
