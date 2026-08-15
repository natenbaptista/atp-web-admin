# enePath WebAdmin — Quick Commands

## Fresh Install (first time)
```bash
chmod +x install.sh
sudo ./install.sh
# Prompts: port (8443), secret key, ATPMGR_DATADIR, log level, SSL CN
# Installs to /opt/enepath/webadmin, creates venv, SSL cert, .env, systemd service
```

## Deploy
```bash
cd ~/atp-dev/webadmin
sudo ./update.sh
```

## Offline customer tarball (no git on site)
```bash
# On a machine that has this checkout (lab / engineer box):
chmod +x pack-update.sh
./pack-update.sh
# writes enepath-webadmin-update-YYYYMMDD.tgz next to this script

# Copy the .tgz to the customer AMP (USB). On site:
tar xzf enepath-webadmin-update-YYYYMMDD.tgz
cd enepath-webadmin-update
sudo ./update.sh
```

## Logs
```bash
# App log (last 50 lines)
sudo tail -50 /var/log/enepath/webadmin.log

# Live log stream
sudo tail -f /var/log/enepath/webadmin.log

# Systemd journal
sudo journalctl -u enepath-webadmin -f
sudo journalctl -u enepath-webadmin -n 50
```

## Service
```bash
sudo systemctl restart enepath-webadmin
sudo systemctl status enepath-webadmin
sudo systemctl stop enepath-webadmin
```

## Dev Mode / Debug
```bash
# Turn ON dev mode or debug
sudo systemctl edit enepath-webadmin
# Add under [Service]:
#   Environment=DEV_MODE=1
#   Environment=LOG_LEVEL=DEBUG

# Apply changes
sudo systemctl daemon-reload && sudo systemctl restart enepath-webadmin

# Revert to defaults (DEV_MODE=0, LOG_LEVEL=INFO)
sudo systemctl revert enepath-webadmin && sudo systemctl restart enepath-webadmin
```

## ATP Backend
```bash
# Check all actor status
atpctl

# Start / stop actors
atpctl start
atpctl stop

# Live config-a1 log
sudo tail -f /var/tmp/config-a1/log

# Test control socket manually
echo '{"type":"controller_echo","payload":"ping"}' | socat - UNIX-CONNECT:/var/tmp/config-a1/control
```

## Config-a1 won't start (wrong IP)
```bash
# Check current IP
ip addr show | grep "inet "

# Check config
cat /var/tmp/config-a1/config.yaml

# Kill stale daemon and restart
kill $(pgrep -f "daemon --name config-a1")
rm -f /var/tmp/pidfiles/config-a1.pid
sleep 2 && atpctl start
```

## .env (production)
```
/opt/enepath/webadmin/.env
```
```bash
sudo cat /opt/enepath/webadmin/.env
```
