#!/usr/bin/env bash
# ============================================================
# pack-update.sh — Build an offline customer update tarball
#
# Same idea as the AMP installer pack: run this on a machine
# that has the webadmin source, copy the .tgz to the customer
# site (USB), extract, run update.sh. No git and no internet
# on the customer box.
#
# There is nothing to compile (Python + static UI).
#
# Usage (from this checkout):
#   chmod +x pack-update.sh
#   ./pack-update.sh
#   ./pack-update.sh /tmp/enepath-webadmin-update.tgz
#
# On the customer AMP:
#   tar xzf enepath-webadmin-update-YYYYMMDD.tgz
#   cd enepath-webadmin-update
#   sudo ./update.sh
# ============================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
die()     { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

[[ -f main.py && -f update.sh && -d routers ]] || \
  die "Run this from the webadmin checkout (need main.py, update.sh, routers/)."

STAMP="$(date +%Y%m%d)"
OUT="${1:-$SCRIPT_DIR/enepath-webadmin-update-${STAMP}.tgz}"
NAME="enepath-webadmin-update"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/webadmin-pack.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT

info "Staging $NAME from $SCRIPT_DIR"

mkdir -p "$STAGE/$NAME"
# Full tree so customer update.sh (rsync --delete) is safe.
EXCL=(
  --exclude='.git'
  --exclude='.gitignore'
  --exclude='venv'
  --exclude='__pycache__'
  --exclude='*.pyc'
  --exclude='.env'
  --exclude='frontend'
  --exclude='.pytest_cache'
  --exclude="$NAME"
  --exclude='enepath-webadmin-update-*.tgz'
)
if command -v rsync >/dev/null 2>&1; then
  rsync -a "${EXCL[@]}" "$SCRIPT_DIR/" "$STAGE/$NAME/"
else
  tar -C "$SCRIPT_DIR" "${EXCL[@]}" -cf - . | tar -C "$STAGE/$NAME" -xf -
fi

# Bump Web version in the staged tree only so a USB pack increments
# independently of AMP. The source checkout is left unchanged (safer
# than rewriting the git-tracked amp_web_version before rsync).
STAGE_VER="$STAGE/$NAME/amp_web_version"
if [[ -f "$STAGE_VER" ]]; then
  cur="$(tr -cd '0-9' < "$STAGE_VER")"
  if [[ -n "$cur" ]]; then
    next=$((10#$cur + 1))
    printf '%s\n' "$next" > "$STAGE_VER"
    info "Staged amp_web_version: $cur → $next"
  else
    info "amp_web_version has no integer; copied as-is"
  fi
else
  info "amp_web_version missing; pack will not bump Web version"
fi

# Do not ship leftover patch helpers; the packed files are already applied.
rm -f "$STAGE/$NAME/button-colors.patch" \
      "$STAGE/$NAME/patch_frontend_colors.py" \
      "$STAGE/$NAME/pack-update.sh"

cat > "$STAGE/$NAME/CUSTOMER.txt" << 'TXT'
enePath webadmin — customer update (offline)
===========================================

No internet. No git.

  tar xzf enepath-webadmin-update-YYYYMMDD.tgz
  cd enepath-webadmin-update
  sudo ./update.sh

That rsyncs this tree onto /opt/enepath/webadmin and restarts
enepath-webadmin. .env and SSL on the box are left alone.

Check
  Lines search: type 24, table shows 2400...
  Line dropdown: appearances like 6000--1 / 6000--2
  GET /button-colors returns the colour list
  Sidebar footer: AMP vX.Y.Z plus Web v N (from amp_web_version)
TXT

chmod +x "$STAGE/$NAME/update.sh" "$STAGE/$NAME/install.sh" 2>/dev/null || true

info "Writing $OUT"
mkdir -p "$(dirname "$OUT")"
tar -C "$STAGE" -czf "$OUT" "$NAME"

success "$(du -h "$OUT" | awk '{print $1}')  $OUT"
echo "    On site: tar xzf $(basename "$OUT") && cd $NAME && sudo ./update.sh"
