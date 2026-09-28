# Plex to Trakt Webhook Scrobbler

[![CI](https://github.com/selits/plex-trakt-webhook/actions/workflows/ci.yml/badge.svg)](https://github.com/selits/plex-trakt-webhook/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-2496ed.svg?logo=docker&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green.svg)

A lightweight, modern Python service that receives Plex Media Server webhooks and automatically tracks your TV shows and movies, updating playback status in real-time and marking episodes as watched in your Trakt account.

---

## 🌟 Features

- **Automatic Show & Movie Tracking**: Synchronizes playback in real-time (`media.play`, `media.pause`, `media.stop`) and automatically marks episodes as viewed in Trakt history upon completion (`media.scrobble`).
- **Instant Rating Synchronization**: Automatically syncs star and 1–10 numerical ratings set in Plex (`media.rate`) directly to your Trakt profile for movies, episodes, and entire shows (`/sync/ratings`).
- **Trakt Collection Synchronization**: Automatically syncs newly downloaded or added movies and episodes to your Trakt collection (`library.new` $\to$ `/sync/collection`), recording technical media specifications (resolution, audio codec, audio channels).
- **Library Section Filtering**: Exclude home video, fitness, or personal libraries (`EXCLUDED_LIBRARIES`) or whitelist specific libraries (`ALLOWED_LIBRARIES`) so private files never pollute your Trakt profile.
- **Modern GUID Resolution**: Supports Plex's modern metadata agents (`imdb://`, `tmdb://`, `tvdb://`), TV show year matching for remake disambiguation, and fallback title matching.
- **Robust Multipart Parsing**: Handles Plex's multipart/form-data payloads (both JSON file parts and raw form fields) without validation errors.
- **Smart Pause Handling**: Automatically finalizes scrobbles if playback is paused past the completion threshold (>=80%), preventing Trakt API 422 warnings.
- **Web UI & Device Code OAuth Flow**: Authorize directly in your browser via `/auth` or headlessly via terminal (`python auth.py`) using Trakt's official activation code (`https://trakt.tv/activate`).
- **Resilient Async Trakt Client**: Built on non-blocking `httpx.AsyncClient` with automatic OAuth token refresh on 401, token health telemetry, and exponential backoff on 429 rate limits.
- **Persistent Offline Queue & Retry Worker**: Automatically preserves scrobbles, watches, ratings, and collection additions in a local SQLite database during Trakt API downtime or network outages, retrying in the background until successfully synced.
- **Multi-Channel Push Notifications**: Delivers real-time rich embeds to Discord, messages to Telegram, and lightweight push alerts to Ntfy or Pushover upon scrobbles, ratings, and collection additions.
- **Homelab Observability & Prometheus Metrics**: Built-in `/metrics` endpoint exporting standard Prometheus exposition metrics (request counts, scrobble status, queue depth, active playback sessions, uptime) for Grafana monitoring.
- **1-Click System Backup & Restore**: Export and restore a timestamped `.zip` archive containing your OAuth tokens, SQLite retry database, and co-watch settings directly from the dashboard.
- **Live Playback Observability & Remaining Time**: Real-time animated dashboard card showing active streams (`▶ Currently Streaming` / `⏸ Paused`), client-side clock drift interpolation, dynamic time remaining (`"24m left"`, `"24m left (paused)"`), progress bar, device names, and recently finished media.
- **Integrated Manual Scrobble & Trakt Watchlist**: Search Trakt's global catalog directly from the dashboard to mark any missed movie or episode as watched (`POST /api/scrobble/manual`) or bookmark upcoming titles to your Trakt watchlist (`POST /api/watchlist`) with 1 click.
- **Multi-User Trakt Support**: Link separate Trakt accounts for different Plex users (`/auth?user=username`), allowing household members sharing the server to scrobble to their own profiles.
- **Watch Together (Co-Watching) Engine**: Automatically dual-scrobbles watched TV shows or movies to your partner's Trakt account when you watch together, while leaving solo shows untracked. Manage shared shows directly from your phone or desktop with interactive tag chips, dynamic movie toggle, automatic `.env` merging, and live `👥 Co-Watched` / `👥 Solo` activity badges.
- **Interactive Synthetic Webhook Testing**: Built-in modal and endpoint (`POST /api/test/webhook`) to simulate Plex playback events (playing, paused, scrobble), verify filter rules, inspect co-watching eligibility reasons, and optionally execute live Trakt history and partner dual-sync.
- **Mobile-First Responsive Web Design**: Fully responsive layout designed for all screen sizes (desktop, tablet, and mobile devices like Android and iOS phones) with fluid grids, touch-friendly scrolling, and compact modal dialogs.
- **Sonarr & Radarr Integrations**: Real-time autocomplete for TV series from your Sonarr library (automatically excluding shows already in your whitelist) plus direct webhook endpoints (`/sonarr`, `/radarr`) for instant Trakt collection sync upon download import.
- **Interactive Air-Gapped Demo Mode (`/demo`)**: Explore a fully populated, authenticated dashboard view with realistic mock playback (*Severance S02E01* on Apple TV 4K), 1,428 scrobbles, activity history, linked multi-user accounts, and Co-Watch configuration with zero risk to disk storage or Trakt credentials.
- **Authenticated System Log Viewer**: Inspect live service logs directly from the dashboard via an interactive terminal modal with level filtering (`ALL`, `ERROR`, `WARNING`, `INFO`), keyword search, auto-scroll, and copy-to-clipboard, backed by `journalctl` on Linux systemd and an in-memory ring buffer fallback with automatic secret redaction.
- **Dashboard Admin Security & Screenshot Privacy Shield**: Public visitors see a hardened, privacy-shielded view (masked usernames, completely masked partner account `@●●●●●●●●`, masked server hostname/port `http://●●●●●●●●:●●●●/webhook?token=●●●●●●●●` for safe screenshots, and locked administrative endpoints). Unlock full administrative access and 1-click URL copying anytime with your Webhook Secret.
- **User Whitelist**: Easily limit scrobbling to your specific Plex username so other family members/friends sharing your server don't overwrite your Trakt history.
- **Live Streamlined Dashboard**: Access `http://<server-ip>:<PORT>/` to view Trakt connection health, server uptime, scrobble statistics, recent activity logs with optional 30s auto-refresh, and a repository footer with live version badge.
- **Seedbox & Docker Ready**: Tested and optimized for containerized environments (Ultra.cc, Whatbox, Docker) with non-root security, healthchecks, and `env_file` auto-loading.

---

## 📋 Prerequisites

- **Plex Pass**: Plex requires an active Plex Pass subscription to enable outgoing Webhooks.
- **Trakt Account**: A free account at [Trakt.tv](https://trakt.tv).
- **Environment**: **Python 3.10+** (for bare-metal / systemd) or **Docker & Docker Compose**.

---

## 🚀 Step-by-Step Setup Guide

### 1. Create a Trakt API Application

1. Log in to [Trakt.tv](https://trakt.tv) and go to **[API Applications](https://trakt.tv/oauth/applications)**.
2. Click **"New Application"**.
3. Fill in the fields:
   - **Name**: `Plex Trakt Webhook` (or any name you like)
   - **Description**: `Plex scrobbler` (optional)
   - **Redirect uri**: `urn:ietf:wg:oauth:2.0:oob`
   - *(Note: Permissions checkboxes are no longer required on Trakt; full scrobble and history access is included under the default OAuth scope)*.
4. Click **"Save App"**.
5. Copy your **Client ID** and **Client Secret**.

---

### 2. Configure Environment

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
nano .env
```

Set your configuration:

```ini
TRAKT_CLIENT_ID=your_client_id_from_trakt
TRAKT_CLIENT_SECRET=your_client_secret_from_trakt

# (Recommended) Restrict scrobbling to your Plex username only (leave blank to allow all users)
PLEX_ALLOWED_USERS=your_plex_username

# (Optional) Protect webhook endpoint from unauthorized requests and unlock dashboard admin tools
WEBHOOK_SECRET=your_optional_secret_token

# Host & Port: Use 0.0.0.0 so Plex containers can reach this service
SERVER_HOST=0.0.0.0
SERVER_PORT=8080

# Scrobble behavior
SCROBBLE_MODE=scrobble
SCROBBLE_THRESHOLD=80.0

# Trakt Collection Sync (library.new events)
SYNC_COLLECTION=true
NOTIFY_ON_COLLECTION=true

# Library Section Filtering (Optional)
ALLOWED_LIBRARIES=
EXCLUDED_LIBRARIES=Home Videos, Personal Videos, Fitness

# Homelab & Prometheus Metrics
PROMETHEUS_METRICS_ENABLED=true

# (Optional) Real-time notifications (Discord, Telegram, Ntfy, Pushover)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
NTFY_URL=https://ntfy.sh/your_topic
NTFY_AUTH_TOKEN=
PUSHOVER_USER_KEY=
PUSHOVER_API_TOKEN=
NOTIFY_ON_SCROBBLE=true
NOTIFY_ON_RATE=true

# (Optional) Watch Together / Co-Watching
CO_WATCH_USER=partner_username
CO_WATCH_SHOWS=The Bear, Severance, House of the Dragon
CO_WATCH_PLAYERS=Living Room Apple TV, Main TV
CO_WATCH_MOVIES=false

# (Optional) Sonarr & Radarr Integration
SONARR_URL=http://localhost:8989
SONARR_API_KEY=your_sonarr_api_key_here
RADARR_URL=http://localhost:7878
RADARR_API_KEY=your_radarr_api_key_here
```

---

### 3. Authenticate with Trakt (One-Time Setup)

You can authenticate either through your web browser or from the command line:

#### Option A: Via Web Browser (Recommended)
1. Start the server (see background/systemd setup below).
2. Open **`http://<server-ip>:<PORT>/auth`** in your browser.
3. The page will fetch your 8-character activation code. Click the link to **`https://trakt.tv/activate`**, enter the code, and click **Authorize**.
4. The page will automatically detect approval and redirect to your dashboard!

#### Option B: Via Terminal / CLI
```bash
.venv/bin/python auth.py
```
1. Open the activation URL displayed, enter the 8-character code, and authorize.
2. The script will save your tokens to `trakt_tokens.json`.


---

## 🌐 Deploying on a Seedbox (e.g., Ultra.cc, Whatbox, VPS)

Because Plex on modern seedboxes (like **Ultra.cc**) runs inside an isolated container, follow these specific guidelines:

### 1. Find an Open Assigned Port
Ultra.cc reserves specific ports for your slot. Run:
```bash
app-ports show
```
Pick any port for an app you **do not use** (e.g. from `app-ports show`). Confirm it's free:
```bash
ss -tuln | grep <PORT>
```
*(If it returns empty, the port is free to use).*

### 2. Network Configuration: Why `0.0.0.0` is Required
In `.env`, always set:
```ini
SERVER_HOST=0.0.0.0
SERVER_PORT=<PORT>
```

If set to `127.0.0.1`, the service will only accept connections from the host and will block incoming requests from the Plex container.

### 3. Auto-Starting on Server Reboots & Running 24/7

Choose one of the methods below to keep the scrobbler running in the background and ensure it automatically restarts if the server reboots:

#### Option A: systemd User Service (Recommended for Ultra.cc, Seedboxes & VPS)
Modern seedboxes (like **Ultra.cc**) and Linux servers support user-level `systemd` services without needing `sudo`. This automatically restarts the service on server boot and recovers from crashes.

1. Copy the provided service file to your systemd user directory:
   ```bash
   mkdir -p ~/.config/systemd/user
   cp plex-trakt.service ~/.config/systemd/user/
   ```
   *(Note: The service file uses `%h/plex-trakt-webhook`. If your repository folder is named or located differently, adjust `WorkingDirectory` and `ExecStart` inside `~/.config/systemd/user/plex-trakt.service` accordingly).*

2. Enable lingering so the service starts on boot without requiring an active SSH session:
   ```bash
   loginctl enable-linger $USER
   ```
   *(On Ultra.cc, lingering is typically enabled by default).*

3. Reload systemd, enable, and start the service:
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now plex-trakt.service
   ```

4. **Useful management commands:**
   ```bash
   # Check service status
   systemctl --user status plex-trakt.service

   # View live logs
   journalctl --user -u plex-trakt.service -f

   # Restart or stop the service
   systemctl --user restart plex-trakt.service
   systemctl --user stop plex-trakt.service
   ```

---

#### Option B: Cron `@reboot` (Universal Fallback for Shared Seedboxes)
If your host does not support user systemd services:
1. Run `crontab -e`.
2. Add the following line at the end (adjusting the path to your repository):
   ```bash
   @reboot /home/<username>/plex-trakt-webhook/start.sh >> /home/<username>/plex-trakt-webhook/server.log 2>&1 &
   ```

---

#### Option C: Running with Screen (Manual / Temporary)
If you only want to run it during testing without surviving server reboots:

```bash
# Start a new screen session
screen -S plex-trakt

# Inside the screen session, run:
.venv/bin/python main.py
```

- **Detach from screen** (keeps it running): Press **`Ctrl+A`** followed by **`D`**.
- **Re-attach to check live logs**: `screen -x plex-trakt`
- **Stop screen**: `pkill -f "screen.*plex-trakt"`

---

#### Option D: Docker & Docker Compose
If you prefer running in a container:

1. Configure your `.env` file (set `TRAKT_TOKENS_FILE=/app/data/trakt_tokens.json`).
2. Start the service with Docker Compose:
   ```bash
   docker compose up -d
   ```
3. Check container logs and built-in health check:
   ```bash
   docker compose logs -f
   ```
   *(Data is persisted in the `./data` volume, and the container runs under a hardened, non-root `appuser`)*.

---

## 🔗 Adding the Webhook in Plex

1. Open **Plex Web** (`https://app.plex.tv/desktop`).
2. Go to **Settings (wrench icon) &rarr; Webhooks** (under your Account settings).
3. Click **Add Webhook**.
4. Enter your webhook URL:
   - **Without Webhook Secret**:
     ```text
     http://<servername>.usbx.me:<PORT>/webhook
     ```
     *(Example: `http://your-server.usbx.me:8080/webhook`)*
   - **With Webhook Secret** (if `WEBHOOK_SECRET` is set in `.env`):
     ```text
     http://<servername>.usbx.me:<PORT>/webhook?token=YOUR_WEBHOOK_SECRET
     ```
   - **For Local PC / Docker on same LAN**:
     ```text
     http://<local-lan-ip>:8080/webhook
     ```
   > ⚠️ **Important for Ultra.cc / Seedboxes:** Do **not** use `127.0.0.1`! Because Plex runs inside an isolated container, `127.0.0.1` points inside the container itself instead of your server host.
5. Click **Save Changes**.

---

## 🗺️ Web UI & API Endpoints

| Endpoint | Method | Description |
| :--- | :---: | :--- |
| **`/`** | `GET` | **Live Web Dashboard**: Real-time connected Trakt profiles, active stream status, co-watch whitelist, and live activity. |
| **`/demo`** | `GET` | **Demo Dashboard**: Air-gapped preview environment showcasing full dashboard with realistic mock data and zero secret exposure. |
| **`/auth`** | `GET` | **Trakt Device Authorization**: Browser-based OAuth activation (support `?user=username` for multi-user linking). |
| **`/webhook`** | `POST` | **Plex Webhook Endpoint**: Receives and processes Plex playback, rating, scrobble, and library additions. |
| **`/health`** | `GET` | **Healthcheck**: Returns JSON status, authentication state, and token health telemetry. |
| **`/metrics`** | `GET` | **Prometheus Metrics**: Scrape real-time service, playback, and queue metrics in standard exposition format. |
| **`/api/logs`** | `GET` | **System Logs**: Live service logs from `journalctl` or in-memory ring buffer with automatic secret redaction (Admin only). |
| **`/api/events`** | `GET` | **Event History**: Returns recent scrobble, rating, and playback events in JSON. |
| **`/api/events/clear`** | `POST` | **Clear Events**: Resets the in-memory event log (Admin only). |
| **`/api/playback`** | `GET` | **Active Streams**: Returns real-time streaming sessions and recently finished media. |
| **`/api/search`** | `GET` | **Trakt Search**: Search movies and shows across Trakt's global database (Admin only). |
| **`/api/scrobble/manual`** | `POST` | **Manual Scrobble**: 1-click manual history scrobble for any movie or episode (Admin only). |
| **`/api/watchlist`** | `POST` | **Trakt Watchlist**: 1-click bookmark movie or TV show to your Trakt watchlist (Admin only). |
| **`/api/queue`** | `GET` | **Offline Queue**: View pending items, retry counts, and error diagnostics in the SQLite queue. |
| **`/api/queue/retry`** | `POST` | **Retry Queue**: Trigger immediate background processing of pending queue items (Admin only). |
| **`/api/queue/clear`** | `POST` | **Clear Queue**: Purge pending or failed queue items (Admin only). |
| **`/api/backup`** | `GET` | **Download Backup**: Export a timestamped `.zip` containing tokens, retry database, and settings (Admin only). |
| **`/api/restore`** | `POST` | **Restore Backup**: Upload and restore a `.zip` backup archive with Zip Slip security verification (Admin only). |
| **`/api/cowatch`** | `GET` | **Co-Watch Status**: Returns shared shows list, configuration, and linked user profiles. |
| **`/api/cowatch/shows`** | `POST` / `DELETE` | **Shared Shows Manager**: Add or remove TV shows from the Watch Together whitelist (Admin only). |
| **`/api/cowatch/settings`** | `POST` | **Co-Watch Settings**: Dynamically toggle movie co-watching or update runtime settings (Admin only). |
| **`/api/cowatch/sync`** | `POST` | **1-Click Partner Dual Sync**: Manually push any completed media to your partner's Trakt account (Admin only). |
| **`/api/sonarr/shows`** | `GET` | **Sonarr Series Search**: Autocomplete TV series from Sonarr for Co-Watch whitelist, automatically excluding already whitelisted shows (Admin only). |
| **`/api/test/webhook`** | `POST` | **Synthetic Webhook Simulator**: Test and simulate Plex events with dry-run or live Trakt sync (Admin only). |
| **`/sonarr`** | `POST` | **Sonarr Webhook**: Instant Trakt collection sync when Sonarr imports a download. |
| **`/radarr`** | `POST` | **Radarr Webhook**: Instant Trakt collection sync when Radarr imports a download. |


---

## 👥 Watch Together & Multi-User Accounts

### 1. The Co-Watching Dilemma
When couples, roommates, or families watch TV shows together on a shared living room Plex profile, only the primary profile's Trakt account traditionally gets updated. If you try to scrobble everything, your partner's Trakt account gets polluted with shows you watched alone.

### 2. The Solution: Intelligent Dual-Sync
`plex-trakt-webhook` solves this with an integrated **Watch Together Engine**:
- **Shared Shows Whitelist**: Define shows you watch together (e.g., *The Bear*, *Severance*, *Succession*). Shows configured in `.env` are automatically merged with dynamic dashboard additions in `data/cowatch_shows.json` on startup.
- **Automatic Matching**: When you finish an episode of a shared show on your Plex profile, it automatically marks as watched on **both** your Trakt account and your partner's Trakt account.
- **Solo Shows Untouched**: Solo shows, anime, or personal binge sessions are tracked strictly on your own profile.
- **Device Filtering (`CO_WATCH_PLAYERS`)**: Optional rule to only trigger dual-scrobble when playing on shared devices (e.g. `Living Room Apple TV`), preventing dual-sync when you watch in bed on your phone.
- **Movie Co-Watching (`CO_WATCH_MOVIES`)**: Toggle whether all finished movies dual-sync to your partner either via `.env` or dynamically using the dashboard's **"Toggle Movies"** button (`POST /api/cowatch/settings`) with zero service restarts.
- **Interactive Activity Badges**: The activity feed highlights whitelisted shows with `✓ Co-Watching` and offers 1-click `+ Co-Watch` buttons for unlisted shows. Completed items display `👥 Co-Watched` (with partner name and sync reason) or `👥 Solo` (explaining why co-watching was skipped, such as device filter or show whitelist).
- **Mobile-Friendly Web Dashboard**: Add or remove shared shows with interactive tag chips (`[ The Bear ✕ ]`) or click `[+ Co-Watch]` in the activity feed with 0 server restarts.
- **Sonarr Live Autocomplete**: As you type show names into the dashboard, it queries your Sonarr library in real-time, automatically filtering out already whitelisted shows for instant 1-click addition.

### 3. Setting Up Watch Together
1. Add your partner's username in `.env`:
   ```ini
   CO_WATCH_USER=partner_username
   CO_WATCH_SHOWS=The Bear, Severance, House of the Dragon
   CO_WATCH_PLAYERS=Living Room Apple TV, Main TV
   CO_WATCH_MOVIES=false
   ```
2. Link their Trakt account by opening `http://<server>:<PORT>/auth?user=partner_username` and entering their Trakt activation code.
3. Done! Shows in your whitelist will now automatically scrobble to both accounts seamlessly.

### 4. Sonarr & Radarr Integration
Connect Sonarr and Radarr to supercharge your dashboard and collection tracking:
1. **Live Co-Watch Autocomplete**:
   Add your Sonarr credentials to `.env`:

   ```ini
   SONARR_URL=http://localhost:8989
   SONARR_API_KEY=your_sonarr_api_key_here
   ```

   Now when adding shows to your shared list on the dashboard, matching series from your Sonarr library will autocomplete automatically with years and status badges, excluding shows you've already added!
2. **Direct Webhooks for Trakt Collection**:
   * In Sonarr: Go to **Settings &rarr; Connect &rarr; Add Webhook**.
   * URL: `http://<server>:<PORT>/sonarr` (or `http://<server>:<PORT>/sonarr?token=YOUR_SECRET` if `WEBHOOK_SECRET` is set).
   * Triggers: Check **On Download** and **On Upgrade**.
   * Click **Test** and **Save**. Your Trakt collection will now update the moment Sonarr imports a download!
   * (Radarr is also supported using `http://<server>:<PORT>/radarr`).

---

## 📊 Homelab Observability & Prometheus Metrics

`plex-trakt-webhook` includes a built-in, thread-safe Prometheus metrics registry exporting directly on `/metrics` (enabled via `PROMETHEUS_METRICS_ENABLED=true`).

### Prometheus Scrape Configuration
Add the following to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'plex-trakt-webhook'
    metrics_path: '/metrics'
    scrape_interval: 15s
    static_configs:
      - targets: ['<server-ip>:<PORT>']
```

### Exported Metrics

| Metric | Type | Description |
| :--- | :---: | :--- |
| `plex_trakt_uptime_seconds` | Gauge | Total server process uptime in seconds. |
| `plex_trakt_requests_total` | Counter | Incoming HTTP requests labeled by `endpoint` and `status`. |
| `plex_trakt_scrobbles_total` | Counter | Scrobble attempts labeled by `media_type` and `status` (`success`, `queued`). |
| `plex_trakt_ratings_total` | Counter | Ratings synchronized to Trakt labeled by `media_type` and `status`. |
| `plex_trakt_collections_total` | Counter | Media items added to Trakt collection labeled by `media_type` and `status`. |
| `plex_trakt_queue_pending` | Gauge | Current number of pending items in the offline retry queue. |
| `plex_trakt_active_streams` | Gauge | Current count of active Plex playback sessions. |

### 📜 Authenticated System Log Viewer

The web dashboard includes an integrated, real-time terminal log viewer accessible via the **"📜 View Logs"** button in the System Operations card (protected by the `WEBHOOK_SECRET` admin authorization gate):
- **Live Search & Filtering**: Filter logs in real-time by keyword, show title, or log level (`ALL`, `ERROR`, `WARNING`, `INFO`).
- **Dual Log Source**: Automatically queries `journalctl --user -u plex-trakt` when running as a systemd user service on Linux, with transparent fallback to an in-memory 1,000-line `RingBufferLogHandler` for containerized (Docker) and local development.
- **Strict Privacy Redaction**: Automatically sanitizes sensitive tokens, query parameters (`?token=...`), Bearer authorization headers, and webhook secrets from all emitted log lines.
- **Convenience Controls**: Auto-scroll to bottom, 1-click copy-to-clipboard, and manual refresh.

---

## 🛡️ Persistent Offline Queue & Disaster Recovery

### 1. Resilient Offline Queue (SQLite)
If Trakt experiences API downtime (HTTP 5xx), rate limits (HTTP 429), or your server temporarily loses internet connectivity:
- The service automatically enqueues the failed scrobble, rating, or collection event into `data/queue.db`.
- A background worker attempts to drain the queue at regular intervals (`QUEUE_RETRY_INTERVAL`, default 300s) with exponential backoff.
- The web dashboard displays live queue depth and allows 1-click **"Retry Queue Now"** or **"Clear Queue"** directly from the UI.

### 2. 1-Click System Backup & Restore
Safeguard your multi-user tokens, offline retry database, and watch-together configuration without manual file copying:
- **Download Backup**: Click **"Download Backup (.zip)"** on the dashboard or request `GET /api/backup` (Admin only) to receive a timestamped archive.
- **Restore Backup**: Drag and drop your `.zip` archive or send `POST /api/restore` (Admin only). The server automatically applies Zip Slip security path validation, unpacks the configuration, and refreshes active Trakt clients without rebooting.

---

## 🗃️ Library Filtering & Trakt Collection Sync

### 1. Library Section Filtering
Prevent personal or non-commercial media from polluting your Trakt history:
- `ALLOWED_LIBRARIES`: Comma-separated whitelist (e.g. `Movies, 4K Movies, TV Shows`). Only webhooks from these sections are processed.
- `EXCLUDED_LIBRARIES`: Comma-separated blacklist (e.g. `Home Videos, Fitness, Personal Recordings`). Any webhook matching an excluded library is discarded immediately.

### 2. Trakt Collection Sync
When `SYNC_COLLECTION=true`, newly added media (`library.new` events) automatically syncs to Trakt's collection (`/sync/collection`):
- Technical media specifications are parsed and submitted to Trakt:
  - **Resolution**: `4k`, `1080p`, `720p`, `480p`, `sd`
  - **Audio Codec**: `dolby_truehd`, `dts_hd_ma`, `dolby_digital_plus`, `aac`, `flac`, etc.
  - **Audio Channels**: `7.1`, `5.1`, `2.0`

---

## 🔔 Multi-Channel Notifications

Deliver real-time alerts whenever a movie or episode is scrobbled, rated, or added to your Trakt collection:

- **Discord**: Set `DISCORD_WEBHOOK_URL` to receive rich embeds with poster art, ratings, and clickable buttons linking directly to Trakt.
- **Telegram**: Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to receive HTML-formatted playback updates.
- **Ntfy**: Set `NTFY_URL` (e.g. `https://ntfy.sh/your_topic`) and optional `NTFY_AUTH_TOKEN` for lightweight mobile push notifications.
- **Pushover**: Set `PUSHOVER_USER_KEY` and `PUSHOVER_API_TOKEN` for native push alerts on iOS and Android.

Toggles:
- `NOTIFY_ON_SCROBBLE=true`: Alerts on finished playback (`media.scrobble` / `scrobble_stop`).
- `NOTIFY_ON_RATE=true`: Alerts when rating media in Plex.
- `NOTIFY_ON_COLLECTION=true`: Alerts when new media is added to your collection.

---

## 💡 Troubleshooting & FAQ

### 1. `422 Unprocessable Content`
Plex sends webhooks with `filename="payload.json"` multipart file parts. The scrobbler is already built to handle both file streams and standard form fields. If you see this error, ensure you have pulled the latest code from GitHub.

### 2. `Trakt 409 Conflict/Already scrobbled` in logs
This is normal and expected! When you finish an episode, Plex sends `media.scrobble` (marking it as watched). Immediately afterward, Plex closes the player and fires `media.stop`. Trakt simply informs the scrobbler that the media was already scrobbled. The service logs this as an informational event and returns `200 OK`.

### 3. `"message":"Progress is XX%. Use stop to scrobble."`
Trakt considers any playback past 80% to be completed. If you pause a video after 80%, calling `/scrobble/pause` causes Trakt to return this message. The scrobbler automatically checks `SCROBBLE_THRESHOLD` and routes late pauses to `/scrobble/stop`.

### 4. How to verify the service is running
Run a quick health check from your terminal:
```bash
curl http://localhost:8080/health
```
Expected response:
```json
{"status":"healthy","authenticated":true,"trakt_user":"your_trakt_username","allowed_users":["your_plex_username"],"scrobble_mode":"scrobble","webhook_secret_enabled":false}
```

---

## 🏗️ Architecture & Codebase Structure

`plex-trakt-webhook` follows a clean, modular Python package architecture:

```text
plex-trakt-webhook/
├── app/
│   ├── clients/             # External API integrations
│   │   ├── trakt_client.py  # Trakt OAuth device flow, scrobbling, ratings, collection sync & search
│   │   └── sonarr_client.py # Sonarr/Radarr API client, webhook parsers & resolution mapping
│   ├── services/            # Core business logic services
│   │   ├── cowatch_manager.py  # Watch Together whitelist & dual-scrobble rules engine
│   │   ├── demo_manager.py     # Air-gapped mock playback, stats, and activity generator
│   │   ├── log_manager.py      # Systemd journalctl reader, in-memory ring buffer & secret redaction
│   │   ├── notifier.py         # Multi-channel notifications (Discord, Telegram, Ntfy, Pushover)
│   │   ├── playback_manager.py # Active streaming sessions & dashboard cards
│   │   ├── queue_manager.py    # Persistent SQLite offline retry queue & background worker
│   │   └── user_manager.py     # Multi-user account client cache & token persistence
│   ├── templates/           # Clean, externalized HTML/CSS/JS dashboard and auth views
│   │   ├── dashboard.html   # Main real-time status dashboard view
│   │   ├── auth.html        # Trakt device activation view
│   │   └── auth_locked.html # Admin authorization gate view
│   ├── config.py            # Centralized environment & directory configuration
│   ├── main.py              # FastAPI application, route handlers & template rendering
│   ├── metrics.py           # Thread-safe Prometheus metrics registry & exposition formatter
│   └── plex_parser.py       # Plex multipart/JSON webhook parsing & media models
├── main.py                  # Backward-compatible service entrypoint (Uvicorn)
├── auth.py                  # Standalone CLI device code authentication tool
├── plex-trakt.service       # systemd user service unit
├── start.sh                 # Portable startup wrapper script
├── upgrade.sh               # 1-click automated upgrade script
├── Dockerfile               # Multi-stage hardened non-root container image
└── tests/                   # Comprehensive pytest test suite (90 tests)
```

---

## 🔄 Updating / Upgrading

To update your installation to the latest release on your server or seedbox:

```bash
./upgrade.sh
```
This automated script:
1. Fetches the latest code from GitHub (`git fetch && git reset --hard origin/main`).
2. Updates dependencies in your virtual environment (`.venv`).
3. Refreshes and enables the `systemd` user service unit (`systemctl --user enable plex-trakt`).
4. Ensures user background lingering is enabled (`loginctl enable-linger`).
5. Restarts the service cleanly and outputs its live running status.

---

## 🧪 Testing

To run the automated test suite locally:

```bash
.venv/bin/pytest -v
```
