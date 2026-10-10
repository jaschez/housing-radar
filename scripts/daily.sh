#!/usr/bin/env bash
# Daily run: sweep -> evaluation -> e-mail. Idempotent within the day: it does not sweep
# again if to_evaluate.json exists, re-evaluate if evals.json exists, or re-send if
# .sent_daily exists. Workspace: $RADAR_WORKSPACE, default ./workspace in the repo.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export RADAR_WORKSPACE="${RADAR_WORKSPACE:-$REPO/workspace}"
PY="$REPO/.venv/bin/python"; [ -x "$PY" ] || PY=python3
TODAY=$("$PY" -c "import sys; sys.path.insert(0, '$REPO/radar'); import workspace; print(workspace.today(workspace.load_config()))") || exit 1
OUT="$RADAR_WORKSPACE/out/$TODAY"
mkdir -p "$OUT" "$RADAR_WORKSPACE/logs"
exec >>"$RADAR_WORKSPACE/logs/$TODAY.log" 2>&1
echo "== $(date -Is) daily $TODAY"

# Same lock as watch.sh: the sweep and the fast cadence never write at the same time.
exec 9>"$RADAR_WORKSPACE/.radar.lock"
flock -w 900 9 || { echo "lock busy for 15 min: aborting"; exit 1; }

if [ ! -s "$OUT/to_evaluate.json" ]; then
  "$PY" "$REPO/radar/run.py" --date "$TODAY" || { echo "run.py failed"; exit 1; }
fi
"$PY" "$REPO/radar/evaluate.py" --date "$TODAY" || echo "evaluation failed; the report goes without it"

if [ ! -f "$OUT/.sent_daily" ]; then
  "$PY" "$REPO/radar/report.py" daily --date "$TODAY"
  RC=$?
  if [ $RC -eq 0 ]; then
    "$PY" "$REPO/radar/notify.py" "$OUT/" && "$PY" "$REPO/radar/report.py" mark --date "$TODAY" && touch "$OUT/.sent_daily"
  else
    echo "no daily e-mail (rc=$RC)"
  fi
fi
echo "== end $(date -Is)"
