#!/usr/bin/env bash
# Install systemd *user* timers for one workspace.
#
#   scripts/install-systemd.sh WORKSPACE_DIR [NAME] [--daily HH:MM] [--watch "CALENDAR"] [--tz ZONE] [--enable]
#
# Defaults: NAME = basename of WORKSPACE_DIR, daily at 08:00, watch every 30 min from
# 08:00 to 22:00, time zone from the workspace config (or UTC). Several workspaces get
# independent units: housing-radar-<NAME>-daily and housing-radar-<NAME>-watch.
# Use --watch none to skip the fast cadence. For timers to run while you are logged out:
#   loginctl enable-linger "$USER"
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WS="$(cd "${1:?usage: install-systemd.sh WORKSPACE_DIR [NAME] [options]}" && pwd)"; shift
NAME="$(basename "$WS")"
if [ $# -gt 0 ] && [[ "$1" != --* ]]; then NAME="$1"; shift; fi
DAILY="08:00"; WATCH="*-*-* 08..21:00,30:00"; TZ_NAME=""; ENABLE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --daily) DAILY="$2"; shift 2 ;;
    --watch) WATCH="$2"; shift 2 ;;
    --tz) TZ_NAME="$2"; shift 2 ;;
    --enable) ENABLE=1; shift ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
done
if [ -z "$TZ_NAME" ]; then
  TZ_NAME=$(grep -E '^timezone:' "$WS/config.yaml" 2>/dev/null | sed -E 's/^timezone:\s*"?([^"#]*)"?.*/\1/' | xargs || true)
fi
TZ_SUFFIX=${TZ_NAME:+ $TZ_NAME}
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"
BASE="housing-radar-$NAME"
ENV_PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"

service() {  # $1 = kind, $2 = script, $3 = timeout
  cat > "$UNIT_DIR/$BASE-$1.service" <<UNIT
[Unit]
Description=Housing radar ($NAME): $1
After=network-online.target

[Service]
Type=oneshot
Environment=RADAR_WORKSPACE=$WS
Environment=PATH=$ENV_PATH
WorkingDirectory=$REPO
ExecStart=$REPO/scripts/$2
TimeoutStartSec=$3
UNIT
}

service daily daily.sh 5400
cat > "$UNIT_DIR/$BASE-daily.timer" <<UNIT
[Unit]
Description=Housing radar ($NAME): daily sweep at $DAILY$TZ_SUFFIX

[Timer]
OnCalendar=*-*-* $DAILY:00$TZ_SUFFIX
Persistent=true
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
UNIT
UNITS="$BASE-daily.timer"

if [ "$WATCH" != "none" ]; then
  service watch watch.sh 900
  cat > "$UNIT_DIR/$BASE-watch.timer" <<UNIT
[Unit]
Description=Housing radar ($NAME): fast alerts

[Timer]
OnCalendar=$WATCH$TZ_SUFFIX
RandomizedDelaySec=60

[Install]
WantedBy=timers.target
UNIT
  UNITS="$UNITS $BASE-watch.timer"
fi

systemctl --user daemon-reload
if [ $ENABLE -eq 1 ]; then
  systemctl --user enable --now $UNITS
  systemctl --user list-timers $UNITS --no-pager
else
  echo "Installed. Enable with: systemctl --user enable --now $UNITS"
fi
