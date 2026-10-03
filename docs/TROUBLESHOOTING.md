# Omniscrobble — Troubleshooting & Diagnostics Guide

This guide covers solutions to common webhook errors, diagnostic procedures, Docker networking configurations, and reverse proxy setups for Omniscrobble.

---

## Table of Contents

1. [Common Webhook & Scrobble Errors](#1-common-webhook-scrobble-errors)
   - [Plex 422 Unprocessable Content](#plex-422-unprocessable-content)
   - [Trakt 409 Conflict / "Already scrobbled"](#trakt-409-conflict-already-scrobbled)
   - [Progress is XX%. Use stop to scrobble](#progress-is-xx-use-stop-to-scrobble)
   - [Trakt 401 Unauthorized](#trakt-401-unauthorized)
   - [Simkl 401 Unauthorized / "Invalid client"](#simkl-401-unauthorized--invalid-client)
   - [Trakt 429 Rate Limit Exceeded](#trakt-429-rate-limit-exceeded)
2. [Service Diagnostics & Health Checks](#2-service-diagnostics-health-checks)
3. [Docker Inter-Container Networking](#3-docker-inter-container-networking)
4. [Reverse Proxy & SSL Headers](#4-reverse-proxy-ssl-headers)
5. [Media Server Connectivity & Token Diagnostics](#5-media-server-connectivity-token-diagnostics)

---

## 1. Common Webhook & Scrobble Errors

### Plex 422 Unprocessable Content

**Symptom:**  
Plex webhook deliveries fail with HTTP status `422 Unprocessable Content` in Plex server logs.

**Cause:**  
Plex sends webhooks as multipart form uploads where the JSON data can be delivered either as a form field or as an attached file part (`filename="payload.json"`). 

**Resolution:**  
Omniscrobble automatically parses both multipart file streams and standard form field payloads. Ensure your deployment is running the latest release:
```bash
./upgrade.sh
```
If containerized with Docker, pull the latest image and restart:
```bash
docker compose pull && docker compose up -d
```

---

### Trakt 409 Conflict / "Already scrobbled"

**Symptom:**  
The terminal or dashboard log displays:  
`Trakt scrobble info: 409 Conflict - Already scrobbled`

**Cause:**  
This is normal and expected behavior. When an episode or movie finishes, Plex fires `media.scrobble` (marking the item watched on Trakt). Immediately afterward, closing the player triggers a second event: `media.stop`. When Omniscrobble submits the stop payload, Trakt responds with `409 Conflict` because the media was already registered as watched seconds prior.

**Resolution:**  
No action required. Omniscrobble handles this gracefully, logs it as an informational notice, and returns `200 OK` to your media server.

---

### Progress is XX%. Use stop to scrobble

**Symptom:**  
Logs display:  
`Trakt scrobble warning: message: Progress is 85%. Use stop to scrobble`

**Cause:**  
Trakt's scrobble API considers any playback past 80% to be finished. If you pause a video after 80%, calling Trakt's `/scrobble/pause` endpoint causes Trakt to reject the pause with this notice.

**Resolution:**  
Omniscrobble automatically intercepts late pauses that exceed `EPISODE_SCROBBLE_THRESHOLD` (default: 80%) or `MOVIE_SCROBBLE_THRESHOLD` (default: 90%) and converts them to `/scrobble/stop` requests so your watch history is accurately saved.

---

### Trakt 401 Unauthorized

**Symptom:**  
Webhooks or dashboard actions fail with HTTP status `401 Unauthorized`.

**Cause:**  
The Trakt OAuth access token has expired or was revoked.

**Resolution:**  
1. Omniscrobble includes proactive token refreshing (refreshing tokens within 24 hours of expiration) and automatic 401 retry handling.
2. If the refresh token itself has expired or was invalidated, re-authenticate via the web browser:
   ```text
   http://<your-server-ip-or-domain>:<PORT>/auth
   ```
   Or via the CLI:
   ```bash
   .venv/bin/python auth.py
   ```

---

### Simkl 401 Unauthorized / "Invalid client"

**Symptom:**  
Device PIN polling fails with:  
`Authorization Error: Invalid client credentials. If your Simkl app was registered as 'Server apps & services', please configure your Client Secret in Settings Hub.`

**Cause:**  
Simkl OAuth 2.0 developer applications registered under the **Server apps & services** category strictly enforce `client_secret` verification when exchanging device authorization codes (`POST /oauth2/token`).

**Resolution:**  
1. Open the [Simkl Developer Applications](https://simkl.com/settings/developer/) dashboard and copy your application's **Client Secret**.
2. Open Omniscrobble's web dashboard and navigate to **Settings Hub ⚙️ &rarr; Trackers &rarr; Simkl**.
3. Paste the secret into the **`SIMKL_CLIENT_SECRET`** field.
4. Click **Save & Link Simkl (PIN Flow)** to immediately authorize. Alternatively, define `SIMKL_CLIENT_SECRET=your_client_secret_here` in `.env`.

---

### Trakt 429 Rate Limit Exceeded

**Symptom:**  
Logs report HTTP 429 errors during bulk scrobbling or synchronization.

**Cause:**  
Trakt enforces an API rate limit of 1 request per second for scrobbling endpoints.

**Resolution:**  
Omniscrobble automatically intercepts 429 responses, buffers the failed requests into the SQLite offline queue (`data/queue.db`), and retries them using exponential backoff respecting the upstream `Retry-After` header.

---

## 2. Service Diagnostics & Health Checks

Verify your running instance from any terminal using `curl`:

```bash
curl http://localhost:8080/health
```

### Response Payload Interpretation

```json
{
  "status": "healthy",
  "authenticated": true,
  "trakt_user": "your_trakt_username",
  "allowed_users": ["your_plex_username"],
  "scrobble_mode": "scrobble",
  "webhook_secret_enabled": false
}
```

- **`status`**: `"healthy"` indicates the FastAPI event loop is responsive.
- **`authenticated`**: `true` verifies valid Trakt OAuth tokens exist on disk.
- **`trakt_user`**: The active cloud profile receiving scrobbles.
- **`allowed_users`**: Usernames permitted to scrobble (`PLEX_ALLOWED_USERS`).
- **`scrobble_mode`**: `"scrobble"` tracks real-time progress; `"checkin"` checks in on start.

### Inspecting Live Service Logs

- **Web Dashboard**: Click **Logs** in the dashboard header to open the interactive live terminal modal with search and log level filters.
- **systemd (Linux Host)**:
  ```bash
  journalctl --user -u omniscrobble -f
  ```
- **Docker Compose**:
  ```bash
  docker compose logs -f
  ```

---

## 3. Docker Inter-Container Networking

When running Omniscrobble alongside Plex, Jellyfin, Sonarr, or Radarr in Docker, `localhost` resolves to the container itself, **not** the host machine or sibling containers.

### Option A: Shared Bridge Network (Recommended)

Place all media containers on a shared Docker network (e.g., `media-net`):

```yaml
networks:
  media-net:
    external: true
```

Configure environment URLs using container service names:

```ini
SONARR_URL=http://sonarr:8989
RADARR_URL=http://radarr:7878
JELLYFIN_URL=http://jellyfin:8096
```

### Option B: Docker Host Gateway

If Omniscrobble is containerized but your media server (like Plex) runs natively on the host:

1. Add `host.docker.internal` to your `docker-compose.yml`:
   ```yaml
   extra_hosts:
     - "host.docker.internal:host-gateway"
   ```
2. Reference the host using:
   ```ini
   PLEX_URL=http://host.docker.internal:32400
   ```

---

## 4. Reverse Proxy & SSL Headers

If hosting Omniscrobble behind a reverse proxy (Nginx, Caddy, Traefik, or Cloudflare Tunnels), ensure standard forwarding headers are preserved so OAuth redirect flows and webhooks function correctly.

### Nginx Configuration

```nginx
server {
    server_name omniscrobble.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Caddy Configuration

```caddy
omniscrobble.yourdomain.com {
    reverse_proxy 127.0.0.1:8080
}
```

### Internal Self-Signed SSL Certificates

If your media servers utilize internal self-signed HTTPS certificates, ensure that the URL protocol is configured as `https://` and that your host system's certificate store trusts your local Certificate Authority (CA). Omniscrobble validates HTTPS connectivity securely against standard system trust stores.

---

## 5. Media Server Connectivity & Token Diagnostics

### Testing Server Connectivity from the Dashboard

Open the **⚙️ Settings Hub** modal on the web dashboard:
- Click **Test Connection** under Plex, Jellyfin, or Emby to verify API keys and URLs.
- The dashboard performs an immediate non-blocking test request and displays the server name, version, and latency.

### Inspecting Webhook Deliveries

If a media server is playing media but no scrobbles appear on Trakt:
1. Verify the server is enabled in `.env` (`PLEX_ENABLED=true`, `JELLYFIN_ENABLED=true`, or `EMBY_ENABLED=true`).
2. Check if your username is listed under `PLEX_ALLOWED_USERS`.
3. If `WEBHOOK_SECRET` is configured, verify that `?token=YOUR_SECRET` is appended to the webhook URL in your media server settings.
4. Check the dashboard **Recent Activity** card or `/api/logs` to see the exact incoming event and discard reason.
