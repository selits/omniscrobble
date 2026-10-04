# Omniscrobble Production Deployment & Operations Guide

This guide covers production deployment strategies for **Omniscrobble**, including running 24/7 as a background systemd user service, deploying via Docker & Docker Compose, configuring reverse proxies with HTTPS, setting up firewall rules, and automated maintenance.

<p align="center">
  <a href="README.md"><b>Overview</b></a> •
  <a href="CONFIGURATION.md"><b>Configuration Guide</b></a> •
  <a href="DEPLOYMENT.md"><b>Deployment Guide</b></a> •
  <a href="docs/API.md"><b>API Reference</b></a> •
  <a href="https://selits.github.io/omniscrobble/"><b>Live Demo</b></a>
</p>

---

## 📑 Table of Contents

1. [Prerequisites & Port Selection](#1-prerequisites--port-selection)
2. [Method 1: Docker & Docker Compose (Recommended for Containers)](#2-method-1-docker--docker-compose-recommended-for-containers)
3. [Method 2: systemd User Service (Recommended for Linux & VPS)](#3-method-2-systemd-user-service-recommended-for-linux--vps)
4. [Method 3: Alternative Process Managers (Fallbacks)](#4-method-3-alternative-process-managers-fallbacks)
   - [Cron @reboot](#cron-reboot)
   - [GNU Screen / Tmux](#gnu-screen--tmux)
5. [Reverse Proxy & SSL Setup](#5-reverse-proxy--ssl-setup)
   - [Nginx](#nginx)
   - [Caddy](#caddy)
   - [Traefik (Docker Labels)](#traefik-docker-labels)
   - [Cloudflare Tunnels](#cloudflare-tunnels)
6. [Firewall & Network Troubleshooting](#6-firewall--network-troubleshooting)
7. [Automated Upgrades & Maintenance](#7-automated-upgrades--maintenance)

---

## 1. Prerequisites & Port Selection

### Selecting an Available Port

Omniscrobble listens on port `8080` by default. Before launching, verify that your chosen port is not already occupied by another service on your host:

```bash
ss -tuln | grep 8080
```

*(If this command returns empty, port 8080 is available. If occupied, select an alternative port such as `8088` or `8090` and configure `SERVER_PORT=<PORT>` in your `.env`)*.

### Why `SERVER_HOST=0.0.0.0` is Required

In `.env`, always ensure:

```ini
SERVER_HOST=0.0.0.0
SERVER_PORT=8080
```

- **`0.0.0.0`** binds Omniscrobble to all available network interfaces. This allows Docker containers, local network devices (Apple TV, Smart TVs), and media servers (Plex, Jellyfin, Emby) to reach the service.
- If set to `127.0.0.1`, the server will only accept loopback connections originating from the exact same local process, rejecting webhooks from containerized or external media servers.

---

## 2. Method 1: Docker & Docker Compose (Recommended for Containers)

Running Omniscrobble in Docker isolates all dependencies, provides automated healthchecks, and runs as an unprivileged non-root user (`appuser` UID 10001) for maximum security.

### Docker Compose Configuration

The repository includes a ready-to-run [`docker-compose.yml`](./docker-compose.yml):

```yaml
services:
  omniscrobble:
    build: .
    container_name: omniscrobble
    restart: unless-stopped
    ports:
      - "${SERVER_PORT:-8080}:8080"
    env_file:
      - .env
    environment:
      - SERVER_PORT=8080
      - TRAKT_TOKENS_FILE=/app/data/trakt_tokens.json
    volumes:
      - ./data:/app/data
```

### Launching the Container

1. Configure your `.env` file from the example:

   ```bash
   cp .env.example .env
   nano .env
   ```

2. Start Omniscrobble in the background:

   ```bash
   docker compose up -d
   ```

3. Check container logs and built-in health status:

   ```bash
   docker compose logs -f
   ```

4. Verify the container healthcheck:

   ```bash
   docker inspect --format='{{json .State.Health.Status}}' omniscrobble
   ```

> [!NOTE]
> All persistent state—including OAuth tokens, user settings (`settings.json`), lifetime scrobble statistics (`stats.json`), and the offline SQLite retry queue (`queue.db`)—is safely mounted to `./data:/app/data` and survives container rebuilds.

### Pre-Built Multi-Arch Images (GHCR)

Omniscrobble automatically publishes signed, multi-architecture container images for both `linux/amd64` (Intel/AMD servers) and `linux/arm64` (Apple Silicon, Raspberry Pi 4/5) via GitHub Container Registry on every release:

```bash
docker pull ghcr.io/selits/omniscrobble:latest
```

To run directly via pre-built image without local building:

```bash
docker run -d \
  --name omniscrobble \
  --restart unless-stopped \
  -p 8080:8080 \
  -v $(pwd)/data:/app/data \
  --env-file .env \
  ghcr.io/selits/omniscrobble:latest
```

### Unraid Community Applications Setup

An official Unraid Community Applications template is available in [`templates/unraid-omniscrobble.xml`](./templates/unraid-omniscrobble.xml):

1. On your Unraid server, place `unraid-omniscrobble.xml` in `/boot/config/plugins/dockerMan/templates-user/`.
2. In the Unraid WebGUI, navigate to **Docker** &rarr; **Add Container** &rarr; select **Omniscrobble**.
3. Confirm the default container port (`8080`), host path for `/app/data` (`/mnt/user/appdata/omniscrobble`), and input your `WEBHOOK_SECRET` and optional `CO_WATCH_USER`.
4. Click **Apply**. Omniscrobble will download and launch automatically with non-root permissions.

### Portainer & TrueNAS SCALE Stacks

For Portainer or TrueNAS SCALE, use the official stack template in [`templates/docker-compose.portainer.yml`](./templates/docker-compose.portainer.yml):

1. In **Portainer**, navigate to **Stacks** &rarr; **Add stack**.
2. Select **Web editor**, paste the contents of `templates/docker-compose.portainer.yml`.
3. Under **Environment variables**, define your `WEBHOOK_SECRET`, `SERVER_PORT`, and optional media server secrets.
4. Click **Deploy the stack**.

---

## 3. Method 2: systemd User Service (Recommended for Linux & VPS)

On Linux servers and VPS instances, running Omniscrobble as a **systemd user service** is the gold standard:

- Runs entirely in user-space without root or `sudo` privileges.
- Starts automatically on system boot.
- Automatically recovers and restarts on unexpected crashes.
- Seamlessly integrates with system logging (`journalctl`).

### Step-by-Step Setup

1. **Create the user service directory:**

   ```bash
   mkdir -p ~/.config/systemd/user
   ```

2. **Copy the service definition:**

   ```bash
   cp plex-trakt.service ~/.config/systemd/user/omniscrobble.service
   ```

3. **Verify the unit file paths:**
   Inspect `~/.config/systemd/user/omniscrobble.service`. It uses `%h` (expands automatically to your home directory):

   ```ini
   [Unit]
   Description=Omniscrobble - Universal Media Scrobbler for Plex, Jellyfin, and Emby to Trakt
   After=network.target

   [Service]
   Type=simple
   WorkingDirectory=%h/omniscrobble
   ExecStart=%h/omniscrobble/.venv/bin/python main.py
   Restart=always
   RestartSec=5
   StandardOutput=journal
   StandardError=journal

   [Install]
   WantedBy=default.target
   ```

   > [!NOTE]
   > Ensure `WorkingDirectory` and `ExecStart` match your project directory path.

4. **Enable lingering (Critical):**
   By default, user systemd processes terminate when you log out of your SSH session. Enabling lingering ensures the service starts on boot and runs 24/7 without requiring an active terminal:

   ```bash
   loginctl enable-linger $USER
   ```

5. **Reload, enable, and start the service:**

   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now omniscrobble.service
   ```

### Daily Management Commands

```bash
# Check running status and uptime
systemctl --user status omniscrobble.service

# Stream live real-time service logs
journalctl --user -u omniscrobble.service -f

# Restart or stop the service
systemctl --user restart omniscrobble.service
systemctl --user stop omniscrobble.service
```

---

## 4. Method 3: Alternative Process Managers (Fallbacks)

### Cron `@reboot`

If your hosting provider or environment does not support user-level systemd:

1. Open your user crontab:

   ```bash
   crontab -e
   ```

2. Add the following entry at the bottom:

   ```bash
   @reboot /home/<username>/omniscrobble/start.sh >> /home/<username>/omniscrobble/server.log 2>&1 &
   ```

3. Save and exit. The startup wrapper script (`start.sh`) will launch the virtual environment on server reboots.

### GNU Screen / Tmux

If you want to run Omniscrobble manually during testing:

```bash
# Start a detached screen session
screen -S omniscrobble

# Inside the session, launch the app:
.venv/bin/python main.py
```

- **Detach**: Press `Ctrl + A`, then press `D`.
- **Re-attach**: Run `screen -r omniscrobble`.
- **Terminate**: Run `screen -X -S omniscrobble quit`.

---

## 5. Reverse Proxy & SSL Setup

Exposing Omniscrobble behind a reverse proxy provides clean custom domain URLs, automatic SSL certificates (HTTPS), and centralized access control.

### Nginx

```nginx
server {
    listen 80;
    server_name omniscrobble.<your-domain>.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name omniscrobble.<your-domain>.com;

    ssl_certificate /etc/letsencrypt/live/omniscrobble.<your-domain>.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/omniscrobble.<your-domain>.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;

        # Forwarding headers (Crucial for Omniscrobble secure cookies and URL generation)
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket & Server-Sent Events support (for real-time dashboard updates)
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

### Caddy

Caddy manages SSL certificates automatically:

```caddy
omniscrobble.<your-domain>.com {
    reverse_proxy 127.0.0.1:8080
}
```

### Traefik (Docker Labels)

If using Traefik in your Docker setup, attach the following labels in `docker-compose.yml`:

```yaml
services:
  omniscrobble:
    # ...
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.omniscrobble.rule=Host(`omniscrobble.<your-domain>.com`)"
      - "traefik.http.routers.omniscrobble.entrypoints=websecure"
      - "traefik.http.routers.omniscrobble.tls.certresolver=myresolver"
      - "traefik.http.services.omniscrobble.loadbalancer.server.port=8080"
```

### Cloudflare Tunnels

If using Cloudflare Tunnels (Zero Trust):

1. In the Cloudflare Zero Trust Dashboard, navigate to **Networks &rarr; Tunnels**.
2. Add a Public Hostname pointing to:
   - **Type**: `HTTP`
   - **URL**: `localhost:8080` (or `omniscrobble:8080` if running on the Docker network).

---

## 6. Firewall & Network Troubleshooting

If your media servers cannot send webhooks to Omniscrobble:

### UFW (Ubuntu / Debian)

```bash
# Allow incoming traffic on your configured port
sudo ufw allow 8080/tcp
sudo ufw status
```

### Firewalld (RHEL / Fedora / Rocky)

```bash
sudo firewall-cmd --permanent --add-port=8080/tcp
sudo firewall-cmd --reload
```

### Testing Connectivity Remotely

From your client device or another terminal on the same network:

```bash
# Test HTTP health endpoint
curl -i http://<your-server-ip>:8080/health
```

A healthy response will return `HTTP/200 OK` with JSON health telemetry.

> 🛠️ For webhook payloads, Plex 422 fixes, and reverse proxy configs, see the [**Troubleshooting & Diagnostics Guide**](./docs/TROUBLESHOOTING.md).

---

## 7. Automated Upgrades & Maintenance

Omniscrobble includes a built-in automated upgrade script (`upgrade.sh`) that pulls the latest release from GitHub, updates dependencies in your virtual environment, verifies linger settings, and restarts the systemd user service with zero downtime.

To upgrade anytime:

```bash
./upgrade.sh
```

*(For Docker setups, pull the latest code and rebuild: `git pull origin main && docker compose build && docker compose up -d`)*.
