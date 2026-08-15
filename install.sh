#!/usr/bin/env bash
# ============================================================
# install.sh — enePath WebAdmin installer for Ubuntu 24
#
# Usage:
#   chmod +x install.sh
#   sudo ./install.sh
#
# What it does:
#   1. Prompts for port and config options
#   2. Builds the React frontend IF public/index.html is not already present
#      (run build_frontend.sh on your dev machine first to avoid needing Node.js
#       on the production server — Node.js is NOT required at runtime)
#   3. Creates /opt/enepath/webadmin with a Python venv
#   4. Generates a self-signed SSL certificate
#   5. Creates a .env config file
#   6. Installs a systemd service and starts it
# ============================================================

set -euo pipefail

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()     { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Root check ────────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Run as root: sudo ./install.sh"

# ── Paths ─────────────────────────────────────────────────────────────────────
INSTALL_DIR="/opt/enepath/webadmin"
SSL_DIR="/opt/enepath/ssl"
LOG_DIR="/var/log/enepath"
SERVICE_NAME="enepath-webadmin"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}   enePath WebAdmin — Installation${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ── Interactive prompts ───────────────────────────────────────────────────────

# Reverse proxy choice
echo -e "  ${BOLD}Deployment mode:${NC}"
echo "    1) nginx  — recommended for production (port 443, nginx → uvicorn)"
echo "    2) direct — uvicorn serves HTTPS directly on a custom port"
echo ""
read -rp "  Choice [1/2, default: 1]: " DEPLOY_MODE_INPUT
DEPLOY_MODE="${DEPLOY_MODE_INPUT:-1}"
[[ "$DEPLOY_MODE" == "1" || "$DEPLOY_MODE" == "2" ]] || die "Enter 1 or 2."

if [[ "$DEPLOY_MODE" == "1" ]]; then
    USE_NGINX=1
    read -rp "  HTTPS port for nginx [default: 443]: " PORT
    PORT="${PORT:-443}"
    UVICORN_PORT=8080
    info "nginx mode: nginx on port $PORT → uvicorn on 127.0.0.1:$UVICORN_PORT"
else
    USE_NGINX=0
    read -rp "  HTTPS port for uvicorn [default: 8443]: " PORT
    PORT="${PORT:-8443}"
    UVICORN_PORT="$PORT"
fi
[[ "$PORT" =~ ^[0-9]+$ ]] || die "Port must be a number."

# Secret key
read -rp "  Session secret key [leave blank to auto-generate]: " SECRET_KEY
if [[ -z "$SECRET_KEY" ]]; then
    SECRET_KEY="$(openssl rand -hex 32)"
    info "Auto-generated secret key."
fi

# ATP backend data dir
read -rp "  ATP backend data dir (ATPMGR_DATADIR) [default: /var/tmp/config-a1, type 'dev' for dev mode]: " DATADIR_RAW

# Dev mode
if [[ "${DATADIR_RAW,,}" == "dev" ]]; then
    DATADIR=""
    DEV_MODE="1"
    DUMMY_LOGIN="1"
    warn "No ATPMGR_DATADIR set — running in DEV MODE (no real backend)."
else
    # Apply default if blank, then strip trailing /control if pasted the socket path
    DATADIR="${DATADIR_RAW:-/var/tmp/config-a1}"
    DATADIR="${DATADIR%/control}"
    read -rp "  Enable dev mode? (disables HTTPS redirect, shows errors in browser) [y/N]: " DEV_MODE_INPUT
    if [[ "${DEV_MODE_INPUT,,}" == "y" ]]; then
        DEV_MODE="1"
    else
        DEV_MODE="0"
    fi
    read -rp "  Enable dummy login? (allow admin/admin without ATP backend auth) [y/N]: " DUMMY_LOGIN_INPUT
    if [[ "${DUMMY_LOGIN_INPUT,,}" == "y" ]]; then
        DUMMY_LOGIN="1"
    else
        DUMMY_LOGIN="0"
    fi
fi

# Log level
read -rp "  Log level [DEBUG/INFO/WARNING, default: INFO]: " LOG_LEVEL
LOG_LEVEL="${LOG_LEVEL:-INFO}"

# SSL CN (hostname / IP)
read -rp "  SSL certificate CN (hostname or IP of this server) [default: $(hostname -f 2>/dev/null || echo localhost)]: " SSL_CN
SSL_CN="${SSL_CN:-$(hostname -f 2>/dev/null || echo localhost)}"

echo ""
echo -e "${BOLD}  Summary:${NC}"
echo "    Install dir : $INSTALL_DIR"
if [[ "$USE_NGINX" == "1" ]]; then
echo "    Mode        : nginx → uvicorn (nginx on :$PORT, uvicorn on 127.0.0.1:$UVICORN_PORT)"
else
echo "    Mode        : uvicorn direct (HTTPS on :$PORT)"
fi
echo "    SSL CN      : $SSL_CN"
echo "    Dev mode    : $DEV_MODE$( [[ "$DEV_MODE" == "1" ]] && echo " (errors in browser, no HTTPS redirect)" || true )"
echo "    Dummy login : $DUMMY_LOGIN$( [[ "$DUMMY_LOGIN" == "1" ]] && echo " (admin/admin bypasses ATP auth)" || true )"
echo "    Log level   : $LOG_LEVEL"
[[ -n "$DATADIR" ]] && echo "    ATPMGR_DATADIR: $DATADIR"
echo ""
read -rp "  Proceed? [Y/n]: " CONFIRM
[[ "${CONFIRM,,}" != "n" ]] || { info "Aborted."; exit 0; }
echo ""

# ── Prerequisites ─────────────────────────────────────────────────────────────
info "Checking prerequisites…"

apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    openssl libssl-dev \
    libmagic1 \
    libpango-1.0-0 libpangoft2-1.0-0 \
    build-essential \
    > /dev/null

# nginx (only when chosen as deployment mode)
if [[ "$USE_NGINX" == "1" ]]; then
    info "Installing nginx…"
    apt-get install -y -qq nginx > /dev/null
    success "nginx installed."
fi

# Node.js — only needed if the frontend hasn't been pre-built.
# If public/index.html already exists (built on the dev machine via build_frontend.sh)
# we skip Node.js entirely; it is not needed at runtime.
if [[ ! -f "$SCRIPT_DIR/public/index.html" ]]; then
    info "public/index.html not found — will build frontend on this machine."
    NEED_NODE_BUILD=1
    if ! command -v node &>/dev/null || (( $(node --version 2>&1 | sed 's/v\([0-9]*\).*/\1/') < 18 )); then
        info "Installing Node.js 20 LTS…"
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - > /dev/null 2>&1
        apt-get install -y -qq nodejs > /dev/null
        success "Node.js $(node --version) installed."
    else
        success "Node.js $(node --version) OK (already installed)"
    fi
else
    info "Pre-built frontend found (public/index.html) — skipping Node.js install."
    NEED_NODE_BUILD=0
fi

# Python 3.11+
PY_VER=$(python3 --version 2>&1 | awk '{print $2}')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if (( PY_MAJOR < 3 || (PY_MAJOR == 3 && PY_MINOR < 11) )); then
    info "Python $PY_VER found — need 3.11+, installing python3.11…"
    add-apt-repository -y ppa:deadsnakes/ppa > /dev/null 2>&1
    apt-get install -y -qq python3.11 python3.11-venv python3.11-dev > /dev/null
    PYTHON="python3.11"
else
    PYTHON="python3"
    success "Python $PY_VER OK"
fi

# ── Create directories ────────────────────────────────────────────────────────
info "Creating directories…"
mkdir -p "$INSTALL_DIR" "$SSL_DIR" "$LOG_DIR"
chown atp:atp "$INSTALL_DIR" "$LOG_DIR"

# ── Build React frontend (only if not pre-built) ───────────────────────────────
if [[ "$NEED_NODE_BUILD" == "1" ]]; then
    info "Building React frontend (this may take a minute)…"
    if [[ -d "$SCRIPT_DIR/frontend" && -f "$SCRIPT_DIR/frontend/package.json" ]]; then
        cd "$SCRIPT_DIR/frontend"
        npm install --loglevel=warn > /dev/null
        npm run build > /dev/null
        cd "$SCRIPT_DIR"
        if [[ -f "$SCRIPT_DIR/public/index.html" ]]; then
            success "Frontend built → public/"
        else
            warn "npm run build completed but public/index.html not found — SPA may not be served."
        fi
    else
        warn "frontend/ directory not found — skipping frontend build. WebAdmin will show a 503 for all page routes."
    fi
else
    success "Using pre-built frontend from public/"
fi

# ── Copy application files ────────────────────────────────────────────────────
info "Copying application files to $INSTALL_DIR…"
rsync -a --delete \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.env' \
    --exclude='install.sh' \
    --exclude='build_frontend.sh' \
    --exclude='venv/' \
    --exclude='frontend/' \
    "$SCRIPT_DIR/" "$INSTALL_DIR/"

# Copy doc/ directory (one level up from webadmin-24, if present)
DOC_SRC="$(dirname "$SCRIPT_DIR")/doc"
if [[ -d "$DOC_SRC" ]]; then
    info "Copying doc/ directory…"
    rsync -a --delete "$DOC_SRC/" "$INSTALL_DIR/../doc/"
    success "Docs copied."
else
    warn "doc/ directory not found at $DOC_SRC — docs viewer will show empty sections."
fi

success "Files copied."

# ── Python virtual environment ────────────────────────────────────────────────
info "Creating Python virtual environment…"
if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    $PYTHON -m venv "$INSTALL_DIR/venv"
fi

info "Installing Python dependencies (this may take a minute)…"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip setuptools wheel -q
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q
success "Dependencies installed."

# ── Self-signed SSL certificate ───────────────────────────────────────────────
info "Generating self-signed SSL certificate (CN=$SSL_CN)…"
openssl req -x509 -newkey rsa:4096 \
    -keyout "$SSL_DIR/key.pem" \
    -out    "$SSL_DIR/cert.pem" \
    -days   3650 \
    -nodes \
    -subj "/C=GB/ST=London/L=London/O=enePath/CN=${SSL_CN}" \
    -addext "subjectAltName=DNS:${SSL_CN},IP:127.0.0.1" \
    2>/dev/null
chmod 600 "$SSL_DIR/key.pem"
chown atp:atp "$SSL_DIR/key.pem" "$SSL_DIR/cert.pem"
success "SSL certificate created (valid 10 years)."
info "  Cert: $SSL_DIR/cert.pem"
info "  Key:  $SSL_DIR/key.pem"

# ── .env file ─────────────────────────────────────────────────────────────────
info "Writing .env configuration…"
cat > "$INSTALL_DIR/.env" <<EOF
# enePath WebAdmin configuration
# Generated by install.sh on $(date)

SECRET_KEY=${SECRET_KEY}
DEV_MODE=${DEV_MODE}
DUMMY_LOGIN=${DUMMY_LOGIN}
LOG_LEVEL=${LOG_LEVEL}
LOG_FILE=${LOG_DIR}/webadmin.log
ATPMGR_DATADIR=${DATADIR}
EOF
chmod 600 "$INSTALL_DIR/.env"
chown atp:atp "$INSTALL_DIR/.env"
success ".env written."

# ── systemd service ───────────────────────────────────────────────────────────
info "Creating systemd service ${SERVICE_NAME}…"

if [[ "$USE_NGINX" == "1" ]]; then
    # nginx handles SSL — uvicorn listens on localhost only, plain HTTP
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=enePath WebAdmin (FastAPI)
After=network.target
Wants=network.target

[Service]
Type=exec
User=atp
Group=atp
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/venv/bin/uvicorn main:app \\
    --host 127.0.0.1 \\
    --port ${UVICORN_PORT} \\
    --log-level warning \\
    --access-log
Restart=on-failure
RestartSec=5
StandardOutput=append:${LOG_DIR}/webadmin.log
StandardError=append:${LOG_DIR}/webadmin.log

# Security hardening — not reachable from outside (nginx proxies)
NoNewPrivileges=yes
PrivateTmp=yes
# Allow child processes (amp_tool_app → ping) to open raw sockets
AmbientCapabilities=CAP_NET_RAW

[Install]
WantedBy=multi-user.target
EOF
else
    # Direct mode — uvicorn terminates SSL itself
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=enePath WebAdmin (FastAPI)
After=network.target
Wants=network.target

[Service]
Type=exec
User=atp
Group=atp
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/venv/bin/uvicorn main:app \\
    --host 0.0.0.0 \\
    --port ${PORT} \\
    --ssl-keyfile  ${SSL_DIR}/key.pem \\
    --ssl-certfile ${SSL_DIR}/cert.pem \\
    --log-level warning \\
    --access-log
Restart=on-failure
RestartSec=5
StandardOutput=append:${LOG_DIR}/webadmin.log
StandardError=append:${LOG_DIR}/webadmin.log

# Security hardening
NoNewPrivileges=yes
# Allow child processes (amp_tool_app → ping) to open raw sockets
AmbientCapabilities=CAP_NET_RAW

[Install]
WantedBy=multi-user.target
EOF
fi

systemctl daemon-reload
systemctl enable  "$SERVICE_NAME" > /dev/null
systemctl restart "$SERVICE_NAME"

# Brief wait to confirm it started
sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
    success "Service started and enabled on boot."
else
    warn "Service may not have started cleanly. Check: journalctl -u $SERVICE_NAME -n 30"
fi

# ── nginx site configuration ─────────────────────────────────────────────────
if [[ "$USE_NGINX" == "1" ]]; then
    NGINX_SITE="/etc/nginx/sites-available/${SERVICE_NAME}"
    info "Writing nginx site config → $NGINX_SITE"
    cat > "$NGINX_SITE" <<EOF
# enePath WebAdmin — nginx reverse proxy
# Generated by install.sh on $(date)

# Redirect HTTP → HTTPS
server {
    listen 80;
    server_name ${SSL_CN};
    return 301 https://\$host:${PORT}\$request_uri;
}

server {
    listen ${PORT} ssl;
    server_name ${SSL_CN};

    ssl_certificate     ${SSL_DIR}/cert.pem;
    ssl_certificate_key ${SSL_DIR}/key.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # Serve Vite-built static assets directly (immutable, long cache)
    location /assets/ {
        alias ${INSTALL_DIR}/public/assets/;
        expires 1y;
        add_header Cache-Control "public, immutable";
        access_log off;
    }

    # Proxy everything else to uvicorn
    location / {
        proxy_pass         http://127.0.0.1:${UVICORN_PORT};
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
        proxy_buffering    off;
    }

    # Increase max upload size for backup restores
    client_max_body_size 512M;
}
EOF

    # Enable site
    ln -sf "$NGINX_SITE" "/etc/nginx/sites-enabled/${SERVICE_NAME}"
    # Disable default site if still enabled
    rm -f /etc/nginx/sites-enabled/default

    nginx -t > /dev/null 2>&1 && systemctl reload nginx || warn "nginx config test failed — check: nginx -t"
    systemctl enable nginx > /dev/null
    success "nginx configured and reloaded."
fi

# ── Firewall hint ─────────────────────────────────────────────────────────────
if command -v ufw &>/dev/null && ufw status | grep -q "Status: active"; then
    if [[ "$USE_NGINX" == "1" ]]; then
        info "Opening ports 80 and $PORT in UFW…"
        ufw allow 80/tcp  > /dev/null
        ufw allow "$PORT/tcp" > /dev/null
        success "UFW rules added (80, $PORT)."
    else
        info "Opening port $PORT in UFW…"
        ufw allow "$PORT/tcp" > /dev/null
        success "UFW rule added."
    fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
SERVER_IP=$(hostname -I | awk '{print $1}')
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  Installation complete!${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}Open in browser:${NC}"
if [[ "$PORT" == "443" ]]; then
echo -e "  ${CYAN}https://${SERVER_IP}${NC}   (accept the self-signed cert warning)"
echo -e "  ${CYAN}https://localhost${NC}"
else
echo -e "  ${CYAN}https://${SERVER_IP}:${PORT}${NC}   (accept the self-signed cert warning)"
echo -e "  ${CYAN}https://localhost:${PORT}${NC}"
fi
echo ""
if [[ "$DUMMY_LOGIN" == "1" ]]; then
echo -e "  ${BOLD}Dummy login credentials:${NC}  admin / admin"
echo ""
fi
echo -e "  ${BOLD}Useful commands:${NC}"
echo "    View logs   : sudo journalctl -u $SERVICE_NAME -f"
echo "    Restart app : sudo systemctl restart $SERVICE_NAME"
echo "    Stop app    : sudo systemctl stop $SERVICE_NAME"
echo "    Edit config : sudo nano $INSTALL_DIR/.env"
if [[ "$USE_NGINX" == "1" ]]; then
echo "    nginx logs  : sudo tail -f /var/log/nginx/error.log"
echo "    Reload nginx: sudo systemctl reload nginx"
echo "    nginx config: $NGINX_SITE"
fi
echo ""
echo -e "  ${BOLD}SSL certificate:${NC}  $SSL_DIR/cert.pem"
echo -e "  ${BOLD}To trust cert in browser,${NC} import $SSL_DIR/cert.pem"
echo -e "  ${BOLD}or download it:${NC}"
echo "    scp root@${SERVER_IP}:${SSL_DIR}/cert.pem ."
echo ""
