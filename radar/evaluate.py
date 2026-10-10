"""Evaluate the day's selection with Claude and write out/<day>/evals.json.

The prompt (radar/prompts/evaluate.md) is generic; the criteria are rendered from the
config and market-specific guidance comes from `evaluation.notes`. The model answers with
a JSON array; this module validates it (only ids from today's batch, required keys) so a
malformed answer never reaches the database.

Usage: python radar/evaluate.py [--date YYYY-MM-DD] [--workspace DIR]
       exit 0 when evals.json exists (written now or earlier), 1 on failure
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "radar"))
import i18n  # noqa: E402
import llm  # noqa: E402
import workspace  # noqa: E402

PROMPT = ROOT / "radar" / "prompts" / "evaluate.md"
FITS = {"High", "Medium", "Low", "Reject"}


def criteria(cfg: dict) -> str:
    cur = cfg.get("currency", "EUR")
    lines = []
    b = cfg.get("budgets") or {}
    if "sale" in b:
        lines.append(f"- Buying: price up to {b['sale']:,} {cur}.")
    if "rent" in b:
        lines.append(f"- Renting: up to {b['rent']:,} {cur} per month"
                     + (f" (below {cfg['rent_floor']} it is usually a room, not a home)." if cfg.get("rent_floor") else "."))
    m = cfg.get("minimums") or {}
    if m:
        lines.append(f"- At least {m.get('area_m2', 0)} m² and {m.get('rooms', 0)} bedrooms.")
    if cfg.get("excluded_property_types"):
        lines.append(f"- Excluded property types: {', '.join(cfg['excluded_property_types'])}.")
    if cfg.get("require_elevator", True):
        lines.append("- Flats above the ground floor need an elevator.")
    reasons = [e["reason"] for e in cfg.get("exclusions") or []]
    if reasons:
        lines.append(f"- Also excluded: {'; '.join(reasons)}.")
    places = [f"{a['name']} ({a.get('region', '')}): {', '.join(a.get('localities') or [])}"
              for a in cfg.get("areas") or []]
    if places:
        lines.append("- Areas searched: " + " | ".join(places) + ".")
    return "\n".join(lines)


def build_prompt(cfg: dict, batch: list[dict]) -> str:
    ev = cfg.get("evaluation") or {}
    lang = i18n.LANGUAGE_NAMES.get(cfg.get("language", "en"), "English")
    return (PROMPT.read_text(encoding="utf-8")
            .replace("{CRITERIA}", criteria(cfg))
            .replace("{NOTES}", (ev.get("notes") or "None.").strip())
            .replace("{LANGUAGE}", lang)
            .replace("{COUNT}", str(len(batch)))
            .replace("{LISTINGS}", json.dumps(batch, ensure_ascii=False, indent=1)))


def parse(text: str, batch: list[dict]) -> list[dict]:
    """The JSON array in the answer, keeping only well-formed entries for today's ids."""
    fenced = re.search(r"```(?:json)?\s*(\[.*\])\s*```", text, re.S)
    raw = fenced.group(1) if fenced else text[text.find("["): text.rfind("]") + 1]
    data = json.loads(raw)
    wanted = {x["listing_id"] for x in batch}
    out = []
    for e in data if isinstance(data, list) else []:
        if isinstance(e, dict) and e.get("listing_id") in wanted and e.get("fit") in FITS:
            out.append({k: e.get(k) for k in ("listing_id", "fit", "priority", "zone_assessment",
                                              "value_assessment", "red_flags", "summary")})
    return out


def run(cfg: dict, out: Path) -> int:
    ev = cfg.get("evaluation") or {}
    backend = ev.get("backend", "claude-cli")
    batch = json.loads((out / "to_evaluate.json").read_text()) if (out / "to_evaluate.json").exists() else []
    if not batch or (out / "evals.json").exists() or backend == "none":
        return 0
    prompt = build_prompt(cfg, batch)
    try:
        text = llm.complete(prompt, backend=backend, model=ev.get("model", "sonnet"))
    except llm.LLMError as e:
        print(f"evaluation failed: {e}")
        return 1
    (out / "evaluate.raw.txt").write_text(text)
    try:
        evals = parse(text, batch)
    except ValueError as e:
        print(f"evaluation answer is not valid JSON ({e}); see evaluate.raw.txt")
        return 1
    (out / "evals.json").write_text(json.dumps(evals, ensure_ascii=False, indent=1))
    print(f"evaluated {len(evals)}/{len(batch)}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--workspace")
    a = ap.parse_args()
    workspace.use(a.workspace)
    cfg = workspace.load_config()
    sys.exit(run(cfg, workspace.out_dir(a.date or workspace.today(cfg))))


if __name__ == "__main__":
    main()
