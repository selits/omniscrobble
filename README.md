<p align="center">
  <img src="docs/assets/banner.png" alt="Omniscrobble Banner" width="750">
</p>

# Omniscrobble — Universal Media Scrobbler & Webhook Bridge

> *Watch anywhere. Track everywhere.*

[![CI](https://github.com/selits/omniscrobble/actions/workflows/ci.yml/badge.svg)](https://github.com/selits/omniscrobble/actions/workflows/ci.yml)
[![Live Demo](https://img.shields.io/badge/demo-live_preview-blue?logo=github&style=flat)](https://selits.github.io/omniscrobble/)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-2496ed.svg?logo=docker&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green.svg)

<p align="center">
  <a href="#-core-capabilities"><b>Features</b></a> •
  <a href="#-quickstart"><b>Quickstart</b></a> •
  <a href="CONFIGURATION.md"><b>Configuration</b></a> •
  <a href="DEPLOYMENT.md"><b>Deployment</b></a> •
  <a href="docs/ARCHITECTURE.md"><b>Architecture</b></a> •
  <a href="docs/API.md"><b>API Reference</b></a> •
  <a href="https://selits.github.io/omniscrobble/"><b>Live Demo</b></a>
</p>

A lightweight, high-performance Python service that receives media server webhooks (Plex, Jellyfin, and Emby) and automatically tracks your TV shows, movies, and anime across Trakt, Simkl, AniList, and MyAnimeList—updating playback status in real-time, syncing ratings and collections, and keeping your watch history synchronized everywhere.

---

## 🌟 Core Capabilities

- **🎬 Universal Media Server Ingestion**: Native webhook support for **Plex** (`/webhook`), **Jellyfin** (`/webhook/jellyfin`), and **Emby** (`/webhook/emby`), standardizing metadata, provider GUIDs (IMDb, TMDb, TVDb), and playback states into a unified scrobble pipeline with media-specific thresholds (`EPISODE_SCROBBLE_THRESHOLD=80`, `MOVIE_SCROBBLE_THRESHOLD=90`).
- **🎯 Multi-Tracker & Anime Engine**: Broadcast playback scrobbles and ratings across **Trakt.tv**, **Simkl**, **AniList (GraphQL)**, and **MyAnimeList (REST v2)** simultaneously. Features automated anime detection heuristics, negative caching (`data/anime_cache.json`), and individual runtime tracker pause toggles.
- **🔄 Two-Way Library Reconciliation**: Bi-directional matching of watched history and star ratings between media servers and cloud trackers (`/api/sync/*`). Includes interactive discrepancy diff modals, selective batch syncing, and automated echo loop suppression (`LoopPreventionManager`).
- **👥 Watch Together (Co-Watching)**: Dual-scrobble shared TV shows and movies to your partner's Trakt profile automatically, with dynamic show whitelists, living room hardware player filters (`CO_WATCH_PLAYERS`), and live Sonarr autocomplete.
- **⚡ Content Bridge & *Arr Automation**: Connect your Trakt Watchlist (`/sync/watchlist`) directly to **Sonarr** and **Radarr** for automated media acquisition, library deduplication, and direct webhook ingestion for instant Trakt collection sync on download.
- **🛡️ Resilience & Homelab Observability**: Persistent SQLite offline retry queue (`data/queue.db`), thread-safe Prometheus metrics (`/metrics`), real-time terminal log viewer with secret redaction, 1-click backup/restore (`.zip`), and an installable PWA dashboard with OLED theme.

---

## 🚦 Integration & Verification Matrix

Omniscrobble is developed in an active daily homelab environment. The matrix below details capabilities that are **verified in live production** by the maintainer versus those that are **implemented and unit-tested to specification awaiting community validation**:

| Category | Service / Platform | Capabilities | Status |
| :--- | :--- | :--- | :--- |
| **Media Servers** | **Plex** | Webhook Ingestion, Scrobble, Rating/Collection Sync, Two-Way Reconciliation | ✅ **Verified** (Maintainer daily driver) |
| | **Jellyfin** | Webhook Ingestion, Scrobble, Rating Sync, Library Reconciliation | 🧪 **Community Beta** (Unit-tested to spec) |
| | **Emby** | Webhook Ingestion, Scrobble, Rating Sync, Library Reconciliation | 🧪 **Community Beta** (Unit-tested to spec) |
| **Trackers** | **Trakt.tv** | Playback Scrobble, Rating Sync, Two-Way Library Reconciliation | ✅ **Verified** (Primary cloud tracker) |
| | **Simkl** | Simultaneous Dual-Scrobble, Rating Sync, Cross-Tracker Sync | ✅ **Verified** (Multi-Tracker engine) |
| | **AniList** | Dedicated GraphQL Anime Scrobble & List Synchronization | 🧪 **Community Beta** (Unit-tested to spec) |
| | **MyAnimeList** | REST v2 Anime Scrobble & Rating Synchronization | 🧪 **Community Beta** (Unit-tested to spec) |
| **Automation** | **Sonarr & Radarr** | Trakt Watchlist Auto-Grab, Download & Collection Sync, Co-Watch Autocomplete | ✅ **Verified** |

> [!TIP]
> **Community Feedback Welcomed**: If you run Jellyfin, Emby, AniList, or MyAnimeList, please report your experience or open an issue on GitHub! 100% of unit tests pass, and your real-world feedback helps verify and refine them.

---

## 🚀 Quickstart

### 1. Prerequisites & Trakt Application

1. **Trakt Account & API App**: Log in to [Trakt.tv](https://trakt.tv), open **[API Applications](https://trakt.tv/oauth/applications)**, click **"New Application"**, set Redirect URI to `urn:ietf:wg:oauth:2.0:oob`, click **"Save App"**, and copy your **Client ID** and **Client Secret**.
2. **Media Server**: Plex (requires active Plex Pass for outgoing webhooks), Jellyfin (with Webhook plugin), or Emby.
3. **Runtime**: **Python 3.10+** (systemd / local) or **Docker & Docker Compose**.

---

### 2. Configure Environment

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
nano .env
```

Configure your essential settings in `.env`:

```ini
# Trakt API Application Credentials (Required)
TRAKT_CLIENT_ID=your_client_id_from_trakt
TRAKT_CLIENT_SECRET=your_client_secret_from_trakt

# Media Server Ingestion (Disabled by default; enable whichever you use)
PLEX_ENABLED=true
JELLYFIN_ENABLED=false
EMBY_ENABLED=false

# (Recommended) Restrict scrobbling to your username (leave blank to allow all users)
PLEX_ALLOWED_USERS=your_username

# (Optional) Webhook Secret: Protects endpoints (?token=...) and locks the admin dashboard
WEBHOOK_SECRET=your_optional_secret_token

# Network Host & Port: Use 0.0.0.0 so containers and LAN devices can connect
SERVER_HOST=0.0.0.0
SERVER_PORT=8080
```

> 💡 **Everything else is optional!**  
> Omniscrobble works immediately with just the settings above. Advanced integrations like **Two-Way Sync**, **Simkl**, **AniList/MAL**, ***Arr Automation**, **Co-Watching**, and **Discord/Telegram alerts** can be configured anytime in `.env` or adjusted live without restarts via the **⚙️ Settings Hub** on the web dashboard.  
> 📖 For the complete reference of all 45+ environment variables, credential retrieval guides, and homelab networking tips, see [**`CONFIGURATION.md`**](./CONFIGURATION.md).

---

### 3. Authenticate with Trakt & Trackers (One-Time Setup)

You can authenticate either through your web browser or from the command line:

#### Trakt Authorization:

##### Option A: Via Web Browser (Recommended)

1. Start the server (see background/systemd setup below).
2. Open **`http://<server-ip>:<PORT>/auth`** in your browser.
3. The page will fetch your 8-character activation code. Click the link to **`https://trakt.tv/activate`**, enter the code, and click **Authorize**.
4. The page will automatically detect approval and redirect to your dashboard!

##### Option B: Via Terminal / CLI

```bash
.venv/bin/python auth.py
```

1. Open the activation URL displayed, enter the 8-character code, and authorize.
2. The script will save your tokens to `trakt_tokens.json`.

> 🎯 **Connecting Secondary Trackers (Simkl, AniList, MyAnimeList):**  
> You can connect Simkl, AniList, and MyAnimeList anytime directly from their dedicated dashboard cards (`/auth/simkl`, `/auth/anilist`, `/auth/mal`). For developer app setup and API key guides, see [**Tracker Developer Applications & OAuth Setup**](./CONFIGURATION.md#4-tracker-developer-applications--oauth-setup) in `CONFIGURATION.md`.

---

## 🌐 Deployment & Running 24/7

Omniscrobble can be deployed natively on any Linux server, VPS, NAS, or containerized environment to run 24/7 in the background and survive system reboots:

| Deployment Mode | Best For | Quick Start |
| :--- | :--- | :--- |
| **systemd User Service** | Linux Servers, VPS, Headless hosts | `systemctl --user enable --now omniscrobble` |
| **Docker Compose** | Containerized environments, Unraid, TrueNAS | `docker compose up -d` |
| **Direct / Shell** | Local testing & development | `python main.py` or `./start.sh` |

> 📖 **Comprehensive Deployment Guide:**  
> For complete instructions on systemd user services, process lingering, port binding, reverse proxy configuration (Caddy / Nginx), Docker Compose persistence, and headless operation, see [**`DEPLOYMENT.md`**](./DEPLOYMENT.md).

---

## 🔗 Media Server Webhook Setup

Add the webhook URL to your media server(s). If `WEBHOOK_SECRET` is set, append `?token=YOUR_WEBHOOK_SECRET` to the URL:

| Media Server | Webhook Destination URL | Required Event Triggers |
| :--- | :--- | :--- |
| **🎬 Plex** | `http://<your-server-ip-or-domain>:<PORT>/webhook` | Default playback & scrobble events |
| **🟣 Jellyfin** | `http://<your-server-ip-or-domain>:<PORT>/webhook/jellyfin` | `Playback Start`, `Progress`, `Stop`, `User Data Saved` |
| **🟢 Emby** | `http://<your-server-ip-or-domain>:<PORT>/webhook/emby` | `playback.start`, `pause`, `stop`, `item.rate`, `markfavorite` |

> 📖 **Step-by-Step Media Server Setup Guides:**  
> For visual walkthroughs, finding server URLs, generating API tokens, and user ID lookup, see [**Media Server Credentials & Webhook Setup**](./CONFIGURATION.md#2-media-server-credentials--webhook-setup) in `CONFIGURATION.md`.

---

## 🗺️ Webhooks & REST API

Omniscrobble provides a real-time web dashboard, universal media server webhook ingestion endpoints, and an extensive REST API for status, telemetry, multi-tracker management, and library reconciliation.

### Primary Ingestion & Management Endpoints

| Endpoint | Method | Purpose |
| :--- | :---: | :--- |
| **`/`** | `GET` | **Live Web Dashboard**: Real-time connected profiles, active streams, and control center. |
| **`/webhook`** | `POST` | **Plex Webhook**: Ingests Plex playback, scrobble, rating, and library events. |
| **`/webhook/jellyfin`** | `POST` / `GET` | **Jellyfin Webhook**: Ingests Jellyfin playback and user data notifications. |
| **`/webhook/emby`** | `POST` / `GET` | **Emby Webhook**: Ingests Emby playback and rating notifications. |
| **`/health`** | `GET` | **Healthcheck**: Returns JSON status, authentication state, and token diagnostics. |
| **`/metrics`** | `GET` | **Prometheus Metrics**: Scrape real-time service, playback, and queue metrics. |

> 📚 **Complete REST API Specification:**  
> For full documentation of all 28 REST endpoints (including multi-tracker management, offline queue, library reconciliation, Co-Watch, manual scrobbling, and backup/restore), see [**`docs/API.md`**](./docs/API.md).

---

## 👥 Watch Together & Multi-User Accounts

When watching movies or TV shows together on a shared living room profile, Omniscrobble automatically dual-scrobbles watch history to **both** your Trakt profile and your partner's profile simultaneously, while keeping solo binges exclusive to your own account.

- **Dynamic Show Whitelist**: Define shared series in `.env` or add them on the fly from the dashboard with live Sonarr autocomplete.
- **Hardware Player Filtering**: Restrict dual-scrobbling to shared living room TVs (`CO_WATCH_PLAYERS`) so bedroom phone viewing stays solo.
- **Movie Co-Watching**: Toggle movie dual-sync with 1 click on the dashboard (`POST /api/cowatch/settings`).

```ini
CO_WATCH_USER=partner_username
CO_WATCH_SHOWS=The Bear, Severance, House of the Dragon
CO_WATCH_PLAYERS=Living Room Apple TV, Main TV
```

Link their account once via `http://<server>:<PORT>/auth?user=partner_username` and shared viewing syncs automatically.

> 📖 **Complete Co-Watching Guide:**  
> For multi-device filtering, movie toggles, and Sonarr/Radarr collection webhooks, see [**Watch Together & Multi-User Accounts**](./CONFIGURATION.md#5-watch-together--multi-user-accounts) in `CONFIGURATION.md`.

---

## 🔄 Two-Way Synchronization & Multi-Server Reconciliation

Standard scrobbling is one-directional (Media Server $\to$ Trakt). Omniscrobble features a bi-directional reconciliation engine that bridges your media server libraries (**Plex**, **Jellyfin**, **Emby**) with your Trakt and Simkl cloud history.

> [!NOTE]
> **Media Server Support Status**:
> - **Plex**: Fully tested, validated, and used daily in active production.
> - **Jellyfin & Emby**: Direct API connection, library scanning, watched status toggling, and rating updates are implemented per official MediaBrowser REST specs and backed by comprehensive unit tests. Because the maintainer's homelab is Plex-only, Jellyfin/Emby direct API reconciliations are currently **Community Beta**. Feedback and bug reports are warmly welcomed!

- **Intelligent GUID Matching**: Compares Trakt cloud history against your libraries via IMDb, TMDb, and TVDb IDs, detecting `Trakt Only`, `Server Only`, and `Rating Mismatch` items.
- **Smart Loop Prevention**: An in-memory TTL cache (`LoopPreventionManager`) drops outgoing webhooks triggered by reconciliation updates, preventing infinite scrobble ping-pong loops.
- **Interactive Diff & Sync UI**: Inspect discrepancies, select specific titles, and trigger 1-click batch syncs with live progress reporting on the web dashboard.

```ini
PLEX_URL=http://<your-server-ip-or-domain>:32400
PLEX_TOKEN=your_plex_token_here
```

> 💡 **Need help finding your server URL, `X-Plex-Token`, or Jellyfin/Emby credentials?**  
> See the [**Media Server Credentials Guide**](./CONFIGURATION.md#2-media-server-credentials--webhook-setup) in `CONFIGURATION.md`.

---

## 📥 Content Bridge & *Arr Automation

The **Content Bridge** connects your personal Trakt Watchlist (`/sync/watchlist`) directly to **Radarr** and **Sonarr**:

- **Automated Ingestion**: Scans your Trakt Watchlist for newly bookmarked movies and shows, checking for duplicates before queuing.
- **Intelligent Routing**: Queries Sonarr/Radarr lookup APIs by TMDb/TVDb ID, automatically selecting valid root folders and quality profiles.
- **Instant Acquisition**: Adds media as monitored and triggers immediate indexer search when `SEARCH_ON_ADD=true`.
- **Ecosystem Health Dashboard**: Monitor server latency, versions, and trigger 1-click manual watchlist syncs directly from the dashboard (`GET /api/ecosystem`).

```ini
AUTO_ADD_FROM_WATCHLIST=true
SEARCH_ON_ADD=true
ARR_WATCHLIST_INTERVAL=3600
```

> 💡 **Looking for Sonarr/Radarr API keys, Quality Profile IDs, or Root Folder setup?**  
> Check the [**Acquisition Stack (*Arr Automation) Guide**](./CONFIGURATION.md#3-acquisition-stack-arr-automation) in `CONFIGURATION.md`.

---

## 📊 Homelab Observability & Prometheus Metrics

Omniscrobble exports standard Prometheus metrics on `/metrics` (`PROMETHEUS_METRICS_ENABLED=true`) tracking uptime, request tallies, scrobbles, ratings, queue depth, and active streams:

```yaml
scrape_configs:
  - job_name: 'omniscrobble'
    metrics_path: '/metrics'
    static_configs:
      - targets: ['<server-ip>:<PORT>']
```

- **Live Redacted Log Viewer**: Integrated dashboard terminal modal (`GET /api/logs`) backed by `journalctl` (systemd) and an in-memory ring buffer (Docker) with keyword search, log-level filters, and automatic secret redaction.
- **Detailed Metrics Specification**: For exact metric types and descriptions, see [**Prometheus Metrics**](./docs/API.md#prometheus-metrics) in `docs/API.md`.

---

## 🛡️ Persistent Offline Queue & Disaster Recovery

- **Resilient SQLite Queue (`data/queue.db`)**: Automatically buffers failed scrobbles, ratings, or collections during Trakt API outages (5xx) or rate limits (429). A background worker drains the queue automatically with exponential backoff.
- **1-Click System Backup & Restore**: Download a timestamped configuration archive (`GET /api/backup`) or drag-and-drop a `.zip` file on the dashboard (`POST /api/restore`) with automated Zip Slip path validation to restore tokens and settings instantly.

---

## 🗃️ Library Filtering & Trakt Collection Sync

- **Section Whitelisting/Blacklisting**: Use `ALLOWED_LIBRARIES="Movies, TV Shows"` or `EXCLUDED_LIBRARIES="Home Videos, Fitness"` to isolate personal libraries from scrobbling.
- **Automated Collection Sync**: When `SYNC_COLLECTION=true`, newly imported media (`library.new`) is submitted directly to your Trakt collection with parsed technical specs (resolution, audio codec, and channels).

---

## 🔔 Multi-Channel Notifications

Deliver instant alerts with poster art, ratings, and direct links when media is scrobbled, rated, or added to your collection:

- **Supported Channels**: **Discord** (`DISCORD_WEBHOOK_URL`), **Telegram** (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`), **Ntfy** (`NTFY_URL`), and **Pushover** (`PUSHOVER_USER_KEY`, `PUSHOVER_API_TOKEN`).
- **Event Toggles**: `NOTIFY_ON_SCROBBLE`, `NOTIFY_ON_RATE`, and `NOTIFY_ON_COLLECTION`.
- **Alert Throttling**: Built-in 30-minute deduplication cooldown prevents spamming notification channels during bulk operations or outages.

> 📖 **Setup Guides:** See [**Multi-Channel Notification Setup**](./CONFIGURATION.md#6-multi-channel-notification-setup) in `CONFIGURATION.md` for bot creation and credential instructions.

---

## 💡 Troubleshooting & FAQ

<details>
<summary><strong>1. Receiving 422 Unprocessable Content on Plex webhooks?</strong></summary>

Plex sends webhooks with `filename="payload.json"` multipart file parts. Omniscrobble automatically handles both multipart file streams and standard form fields. Ensure your deployment is running the latest release.
</details>

<details>
<summary><strong>2. Seeing "Trakt 409 Conflict / Already scrobbled" in logs?</strong></summary>

This is expected and normal. When you finish an episode, Plex sends `media.scrobble` (marking it watched). Immediately afterward, Plex closes the player and fires `media.stop`. Trakt simply reports that the item was already scrobbled; Omniscrobble logs this as an informational event and returns `200 OK`.
</details>

<details>
<summary><strong>3. Seeing "message: Progress is XX%. Use stop to scrobble"?</strong></summary>

Trakt considers playback past 80% to be completed. If you pause a video after 80%, calling `/scrobble/pause` causes Trakt to return this message. Omniscrobble detects late pauses beyond `SCROBBLE_THRESHOLD` and automatically routes them to `/scrobble/stop`.
</details>

<details>
<summary><strong>4. How to verify the service is running?</strong></summary>

Run a health check from your terminal:

```bash
curl http://localhost:8080/health
```

Returns JSON containing token diagnostics, scrobble mode, and allowed users:
```json
{"status":"healthy","authenticated":true,"trakt_user":"your_trakt_username","allowed_users":["your_plex_username"],"scrobble_mode":"scrobble","webhook_secret_enabled":false}
```
</details>

> 🛠️ **More Troubleshooting:** For reverse proxy configs, Docker networking, and SSL certificate troubleshooting, see [**Homelab Networking & Troubleshooting**](./CONFIGURATION.md#8-homelab-networking--troubleshooting) in `CONFIGURATION.md`.

---

## 🏗️ Architecture & Codebase Structure

Omniscrobble is built on an asynchronous FastAPI foundation with decoupled client and service layers:

```text
omniscrobble/
├── app/
│   ├── clients/             # API integrations (Trakt, Simkl, AniList, MAL, Plex, JF, Emby, *Arr)
│   ├── services/            # Core engines (co-watch, reconciliation, multi-tracker, queue, alerts)
│   ├── templates/           # Real-time HTML5/JS responsive web dashboard & auth views
│   ├── config.py            # Centralized settings & environment variables
│   ├── main.py              # FastAPI app, route handlers & webhook ingestion
│   └── metrics.py           # Thread-safe Prometheus metrics registry
├── docs/                    # GitHub Pages static demo (docs/index.html) & API/Architecture specs
├── scripts/                 # Demo compilation & branding asset generators
└── tests/                   # Pytest test suite (171 unit & integration tests)
```

> 🏛️ **Full Architectural Blueprint:**  
> For the complete system architecture diagram, component layers, data flows, and full file-by-file directory manifest, see [**`docs/ARCHITECTURE.md`**](./docs/ARCHITECTURE.md).

---

## 🗺️ Roadmap & Horizons

Omniscrobble is developed with a modular multi-platform architecture.

- [x] **Universal Media Server Ingestion**: Multi-server webhooks for Plex, Jellyfin, and Emby.
- [x] **Multi-Tracker Synchronization**: Simultaneous dispatch across Trakt, Simkl, AniList, and MyAnimeList.
- [x] **Bi-Directional Library Reconciliation**: Discrepancy diffing and played/rating sync across Plex, Jellyfin, Emby, Trakt, and Simkl.
- [x] **Watch Together / Co-Watch**: Dynamic multi-user partner scrobbling with show and device whitelists.
- [x] **Content Bridge**: Automated Trakt Watchlist sync to Sonarr and Radarr.
- [x] **Offline Queue & Resilience**: SQLite persistence with exponential backoff and transient failure retries.
- [ ] **Direct P2P Sync**: Mesh synchronization between distributed Omniscrobble instances.
- [ ] **Dynamic Rules Engine**: In-app scrobble threshold and library filtering configuration editor.

> 📦 **Release History & Changelogs:**  
> For detailed release notes, changelogs, and upgrade instructions for every release (v1.0 &rarr; v2.3+), visit [**GitHub Releases**](https://github.com/selits/omniscrobble/releases).

---

## 🔄 Updating / Upgrading

To update your installation to the latest release on your server or host:

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
