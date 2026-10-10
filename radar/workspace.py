"""Where a radar instance keeps everything that is not code.

The code in this repository is shared. Everything personal lives in a *workspace*:

  <workspace>/config.yaml       search criteria, areas, sources, notifications
  <workspace>/secrets.env       optional credentials (SMTP, API keys). Never commit it.
  <workspace>/plugins/          optional source and fetcher plugins of your own
  <workspace>/data/radar.db     listing history (rows are never deleted)
  <workspace>/out/YYYY-MM-DD/   daily outputs: to_evaluate.json, evals.json, e-mails
  <workspace>/logs/             one log file per day

Resolution order: the --workspace argument (which sets RADAR_WORKSPACE), the
RADAR_WORKSPACE environment variable, and finally ./workspace inside the repository.
Several workspaces can share one checkout, e.g. one per person with their own criteria.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def use(path: str | os.PathLike | None):
    """Select a workspace for the rest of the process (CLI --workspace)."""
    if path:
        os.environ["RADAR_WORKSPACE"] = str(Path(path).expanduser().resolve())


def root() -> Path:
    return Path(os.environ.get("RADAR_WORKSPACE") or REPO / "workspace").expanduser().resolve()


def config_path() -> Path:
    return root() / "config.yaml"


def load_config() -> dict:
    p = config_path()
    if not p.exists():
        raise SystemExit(f"No config at {p}. Copy examples/workspace/config.yaml there and edit it.")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def data_dir() -> Path:
    return root() / "data"


def out_dir(day: str) -> Path:
    d = root() / "out" / day
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = root() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def secrets() -> dict:
    """KEY=VALUE pairs from <workspace>/secrets.env, overridden by the real environment."""
    out: dict[str, str] = {}
    p = root() / "secrets.env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    out.update({k: v for k, v in os.environ.items() if k in out or k.isupper()})
    return out


def today(cfg: dict | None = None) -> str:
    """Today's date in the workspace time zone (config `timezone`, default UTC)."""
    tz = (cfg or {}).get("timezone")
    if tz:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(tz)).date().isoformat()
    return dt.date.today().isoformat()
