#!/usr/bin/env bash
# Fast cadence: first page of each search, hard filter and e-mail. If the daily run holds
# the lock, this round is skipped. Workspace: $RADAR_WORKSPACE, default ./workspace.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export RADAR_WORKSPACE="${RADAR_WORKSPACE:-$REPO/workspace}"
PY="$REPO/.venv/bin/python"; [ -x "$PY" ] || PY=python3
TODAY=$("$PY" -c "import sys; sys.path.insert(0, '$REPO/radar'); import workspace; print(workspace.today(workspace.load_config()))") || exit 1
mkdir -p "$RADAR_WORKSPACE/logs"
exec >>"$RADAR_WORKSPACE/logs/$TODAY.log" 2>&1

exec 9>"$RADAR_WORKSPACE/.radar.lock"
flock -n 9 || { echo "$(date -Is) watch: lock busy, skipping"; exit 0; }

P=$("$PY" "$REPO/radar/watch.py" scan --date "$TODAY")
RC=$?
if [ $RC -eq 0 ]; then
  echo "$(date -Is) watch: e-mail at ${P##*/}"
  "$PY" "$REPO/radar/notify.py" "$P" && "$PY" "$REPO/radar/watch.py" mark "$P"
elif [ $RC -ne 3 ]; then
  echo "$(date -Is) watch: watch.py failed (rc=$RC): $P"
fi
