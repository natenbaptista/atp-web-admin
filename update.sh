#!/usr/bin/env bash
# ============================================================
# update.sh — Re-deploy webadmin code without touching config/SSL
#
# This script runs ON THE SERVER from a staging directory.
# It does NOT push from your dev machine.
#
# Typical workflow:
#   1. On dev machine — copy source to server staging dir:
#        rsync -av --exclude='.env' --exclude='venv/' --exclude='frontend/' \
#          /c/knk/enepath_amp_setup/atp-dev-24/webadmin-24/ \
#          atp@<server>:/home/atp/webadmin-src/
#
#   2. On server — run this script from the staging dir:
#        cd /home/atp/webadmin-src
#        sudo ./update.sh
#
# Usage:
#   sudo ./update.sh
#
# To toggle debug/dev mode, use systemd drop-in overrides:
#   sudo systemctl edit enepath-webadmin
#     [Service]
#     Environment=LOG_LEVEL=DEBUG
#     Environment=DEV_MODE=1
#
# To revert overrides:
#   sudo systemctl revert enepath-webadmin
# ============================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()     { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run as root: sudo ./update.sh"

INSTALL_DIR="/opt/enepath/webadmin"
SERVICE_NAME="enepath-webadmin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[[ -d "$INSTALL_DIR" ]] || die "WebAdmin not installed. Run install.sh first."

info "Syncing files to $INSTALL_DIR…"
rsync -a --delete \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.env' \
    --exclude='install.sh' \
    --exclude='update.sh' \
    --exclude='venv/' \
    "$SCRIPT_DIR/" "$INSTALL_DIR/"

# Sync doc/ if present
DOC_SRC="$(dirname "$SCRIPT_DIR")/doc"
if [[ -d "$DOC_SRC" ]]; then
    info "Syncing doc/…"
    rsync -a --delete "$DOC_SRC/" "$INSTALL_DIR/../doc/"
fi

info "Updating Python dependencies…"
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q

# Ensure the service has CAP_NET_RAW so amp_tool_app can call ping
DROPIN_DIR="/etc/systemd/system/${SERVICE_NAME}.service.d"
DROPIN_FILE="${DROPIN_DIR}/capabilities.conf"
if [[ ! -f "$DROPIN_FILE" ]]; then
    info "Adding CAP_NET_RAW drop-in for amp_tool_app ping support…"
    mkdir -p "$DROPIN_DIR"
    cat > "$DROPIN_FILE" <<'EOF'
[Service]
AmbientCapabilities=CAP_NET_RAW
EOF
    systemctl daemon-reload
    success "Drop-in written: $DROPIN_FILE"
fi

info "Restarting service…"
systemctl restart "$SERVICE_NAME"
sleep 2

if systemctl is-active --quiet "$SERVICE_NAME"; then
    success "Service restarted OK."
    echo "    Tail logs  : sudo journalctl -u $SERVICE_NAME -f"
    echo "    Edit mode  : sudo systemctl edit $SERVICE_NAME"
    echo "    Revert mode: sudo systemctl revert $SERVICE_NAME"
else
    echo "Check logs: journalctl -u $SERVICE_NAME -n 30"
fi
