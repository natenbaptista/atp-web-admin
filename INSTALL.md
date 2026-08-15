# enePath WebAdmin — Install Guide

Assumes: fresh Ubuntu 24 server, ATP backend already running.

---

## 0. Set a Static IP (Netplan)

On a new server the IP will likely be DHCP. Set a static IP **first** — the ATP backend binds to a specific host IP and will fail to start if the IP changes.

Find your network interface name:
```bash
ip link show
# Look for something like: enp0s3, ens18, eth0
```

Edit the Netplan config:
```bash
sudo nano /etc/netplan/50-cloud-init.yaml
# (filename may differ — check with: ls /etc/netplan/)
```

Replace contents with:
```yaml
network:
  version: 2
  ethernets:
    enp0s3:              # ← replace with your interface name
      dhcp4: false
      addresses:
        - 192.168.68.152/22    # ← your static IP / prefix length
      routes:
        - to: default
          via: 192.168.68.1    # ← your gateway
      nameservers:
        addresses: [8.8.8.8, 8.8.4.4]
```

Apply:
```bash
sudo netplan apply
ip addr show | grep "inet "   # verify IP is set
```

> **Why this matters:** ATP actors (config-a1, gateway-a1, mixer-a1) bind to the host IP at startup.
> If the IP changes after they were started, the actors fail to restart with
> `bind: Cannot assign requested address`. Fix: update `/var/tmp/config-a1/config.yaml` and restart actors (see Troubleshooting).

---

## 1. Copy Files to Server

The webadmin source lives in `webadmin/` on the dev machine.
Copy the whole parent directory to the server:

```bash
# From your dev machine — copy webadmin folder
scp -r /path/to/atp-dev/webadmin  atp@<server-ip>:~/atp-dev/webadmin

# If doc/ folder exists alongside webadmin, copy that too
# (used by the in-app documentation viewer)
scp -r /path/to/atp-dev/doc  atp@<server-ip>:~/atp-dev/doc
```

Expected layout on the server:
```
~/atp-dev/
├── webadmin/        ← webadmin contents go here
│   ├── install.sh
│   ├── update.sh
│   ├── main.py
│   ├── requirements.txt
│   └── ...
└── doc/             ← optional, for in-app docs
```

---

## 2. Verify ATP Backend is Running

The webadmin communicates with the ATP `config-a1` actor via a Unix socket.
Make sure it's running and bound to the **correct IP** before installing.

```bash
# Check all ATP actors
atpctl

# Expected: config-a1 should show "running"
# pid        name        status    uptime
# 1234       config-a1   running   00:10
```

If `config-a1` shows **stopped**, the host IP in the source config files is likely wrong.

> **Important:** Do NOT edit `/var/tmp/config-a1/config.yaml` directly.
> `atpctl start` always reads from `~/atp/deploy/nodes/*/config.yaml` and overwrites the runtime copy.
> The source files in `deploy/nodes/` are what you must edit.

```bash
# Check actual machine IP
ip addr show | grep "inet "

# Update host IP in ALL actor source configs (replace 192.168.x.x with actual IP)
SERVER_IP=$(ip -4 addr show | grep "inet " | grep -v "127.0.0" | awk '{print $2}' | cut -d/ -f1 | head -1)
echo "Detected IP: $SERVER_IP"

# Edit each node config — change host: and config_server: to the correct IP
nano ~/atp/deploy/nodes/config-a1/config.yaml
nano ~/atp/deploy/nodes/gateway-a1/config.yaml
nano ~/atp/deploy/nodes/mixer-a1/config.yaml
nano ~/atp/deploy/nodes/mixer-a2/config.yaml
```

In each file, update the `host:` field (and `config_server:` where present),
and set `parameters` to empty string (see note below):
```yaml
# config-a1/config.yaml
actor:
  type: Config
  host: 192.168.68.152      # ← change to your server IP
  port: 2830
  parameters: ""            # ← must be empty, NOT "--use-syslog"

# gateway-a1/config.yaml, mixer-a1/config.yaml, mixer-a2/config.yaml
actor:
  type: Gateway             # (or Mixer)
  host: 192.168.68.152      # ← change to your server IP
  config_server: 192.168.68.152:2830   # ← same IP, port 2830
  parameters: ""            # ← must be empty, NOT "--use-syslog"
```

> **Why `parameters: ""`?**
> The `--use-syslog` flag causes the actor binary to call `daemon()` internally
> (fork + setsid) to detach from the terminal. But `atpctl` already runs each actor
> inside a `daemon` wrapper process. This double-daemonization causes the wrapper to
> see its child exit (after the internal fork) and think it crashed — leading to the
> `fatal: failed to become a daemon: Resource temporarily unavailable` loop.
> Removing `--use-syslog` keeps the actor in the foreground where the wrapper manages
> it correctly. Logs still go to syslog via the wrapper's `--output=user.crit` flag.

Or do it in one shot with sed:
```bash
sed -i 's/parameters: "--use-syslog"/parameters: ""/' ~/atp/deploy/nodes/config-a1/config.yaml
sed -i 's/parameters: "--use-syslog"/parameters: ""/' ~/atp/deploy/nodes/gateway-a1/config.yaml
sed -i 's/parameters: "--use-syslog"/parameters: ""/' ~/atp/deploy/nodes/mixer-a1/config.yaml
sed -i 's/parameters: "--use-syslog"/parameters: ""/' ~/atp/deploy/nodes/mixer-a2/config.yaml

# Verify
grep "parameters" ~/atp/deploy/nodes/*/config.yaml
```

Then restart all actors:
```bash
atpctl start
sleep 3
atpctl         # verify all show "running"
```

Confirm the control socket exists:
```bash
ls /var/tmp/config-a1/control
# Should show:  srwxr-xr-x  1 atp atp  0  ...  /var/tmp/config-a1/control
```

Note this path — you'll enter it as `ATPMGR_DATADIR` during install.

---

## 3. Run the Installer

```bash
cd ~/atp-dev/webadmin
chmod +x install.sh
sudo ./install.sh
```

The installer will prompt for:

| Prompt | What to enter |
|---|---|
| HTTPS port | `8443` (default) or any free port |
| Session secret key | Press Enter to auto-generate (recommended) |
| ATPMGR_DATADIR | Press Enter for default `/var/tmp/config-a1`, or type a custom path. Type `dev` for dev mode (no real backend). Do not enter the socket path (`/var/tmp/config-a1/control`) — the directory only. |
| Log level | `INFO` (default), or `DEBUG` for troubleshooting |
| SSL certificate CN | Server IP or hostname, e.g. `192.168.68.152` |

The installer:
- Installs Python 3 + dependencies via apt
- Creates `/opt/enepath/webadmin/` with a Python venv
- Generates a self-signed SSL cert at `/opt/enepath/ssl/`
- Writes `/opt/enepath/webadmin/.env`
- Creates and starts the `enepath-webadmin` systemd service

---

## 4. Verify It Started

```bash
sudo systemctl status enepath-webadmin
sudo tail -20 /var/log/enepath/webadmin.log
```

Open in browser:
```
https://<server-ip>:8443
```
Accept the self-signed certificate warning.

---

## 5. Deploy Updates (after first install)

After code changes on the dev machine, copy and deploy:

```bash
# Copy updated files to server
scp -r /path/to/atp-dev/webadmin  atp@<server-ip>:~/atp-dev/webadmin

# On the server — deploy without touching .env or SSL
cd ~/atp-dev/webadmin
sudo ./update.sh
```

---

## Key Paths

| Path | What |
|---|---|
| `/opt/enepath/webadmin/` | Installed app |
| `/opt/enepath/webadmin/.env` | Config (SECRET_KEY, ATPMGR_DATADIR, etc.) |
| `/opt/enepath/ssl/cert.pem` | SSL certificate |
| `/opt/enepath/ssl/key.pem` | SSL private key |
| `/var/log/enepath/webadmin.log` | App log |
| `/var/tmp/config-a1/control` | ATP backend socket |
| `/etc/systemd/system/enepath-webadmin.service` | Systemd service |

---

## Troubleshooting

**Cannot connect to ATP backend**
```bash
# Check config-a1 is running
atpctl

# If stopped, check config.yaml has correct IP
cat /var/tmp/config-a1/config.yaml
ip addr show | grep "inet "

# If IP is wrong, update config.yaml and restart:
kill $(pgrep -f "daemon --name config-a1")
rm -f /var/tmp/pidfiles/config-a1.pid
sleep 2 && atpctl start
```

**Service won't start**
```bash
sudo journalctl -u enepath-webadmin -n 30
sudo tail -30 /var/log/enepath/webadmin.log
```

**Enable debug mode temporarily**
```bash
sudo systemctl edit enepath-webadmin
# Add:
# [Service]
# Environment=LOG_LEVEL=DEBUG
# Environment=DEV_MODE=1

sudo systemctl daemon-reload && sudo systemctl restart enepath-webadmin

# Revert when done:
sudo systemctl revert enepath-webadmin && sudo systemctl restart enepath-webadmin
```

**Check what .env has**
```bash
sudo cat /opt/enepath/webadmin/.env
```
