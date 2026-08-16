#!/usr/bin/env bash
# Pull main, then rsync onto /opt/enepath/webadmin and restart.
# Run from this checkout (a clone of natenbaptista/atp-web-admin).
set -euo pipefail
cd "$(dirname "$0")"
[[ -d .git ]] || { echo "Not a git checkout. Clone https://github.com/natenbaptista/atp-web-admin.git first." >&2; exit 1; }
git pull --ff-only origin main
exec sudo ./update.sh
