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
  <a href="#-documentation-hub"><b>Documentation</b></a> •
  <a href="CONFIGURATION.md"><b>Configuration</b></a> •
  <a href="DEPLOYMENT.md"><b>Deployment</b></a> •
  <a href="DEVELOPMENT.md"><b>Development</b></a> •
  <a href="docs/LOCAL_TESTING.md"><b>Local Testing</b></a> •
  <a href="docs/FEATURES.md"><b>Guides</b></a> •
  <a href="docs/ARCHITECTURE.md"><b>Architecture</b></a> •
  <a href="docs/API.md"><b>API</b></a> •
  <a href="docs/TROUBLESHOOTING.md"><b>FAQ</b></a> •
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

### 1. Prerequisites

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

> 💡 **Everything else is optional!** Advanced integrations like Two-Way Sync, Simkl, AniList/MAL, *Arr Automation, Co-Watching, and Discord/Telegram alerts can be configured anytime in `.env` or adjusted live without restarts via the web dashboard. See [**`CONFIGURATION.md`**](./CONFIGURATION.md) for the complete reference.

---

### 3. Authenticate with Trakt & Trackers

Start Omniscrobble and authenticate via browser (recommended) or terminal:

- **Via Web Browser**: Open `http://<server-ip>:<PORT>/auth`, click the link to [trakt.tv/activate](https://trakt.tv/activate), enter the 8-character activation code, and click Authorize.
- **Via CLI**: Run `.venv/bin/python auth.py` and follow the terminal instructions.

Secondary trackers (**Simkl**, **AniList**, **MyAnimeList**) can be connected anytime directly from their dedicated dashboard cards (`/auth/simkl`, `/auth/anilist`, `/auth/mal`).

---

### 4. Configure Media Server Webhooks

Add the webhook URL to your media server. If `WEBHOOK_SECRET` is set, append `?token=YOUR_WEBHOOK_SECRET`:

| Media Server | Webhook Destination URL | Required Event Triggers |
| :--- | :--- | :--- |
| **🎬 Plex** | `http://<your-server-ip-or-domain>:<PORT>/webhook` | Default playback & scrobble events |
| **🟣 Jellyfin** | `http://<your-server-ip-or-domain>:<PORT>/webhook/jellyfin` | `Playback Start`, `Progress`, `Stop`, `User Data Saved` |
| **🟢 Emby** | `http://<your-server-ip-or-domain>:<PORT>/webhook/emby` | `playback.start`, `pause`, `stop`, `item.rate`, `markfavorite` |

---

## 📚 Documentation Hub

For in-depth guides, variable references, and operational walk-throughs, explore the sub-documents below:

| Guide | Description & Scope |
| :--- | :--- |
| [**Configuration Reference**](./CONFIGURATION.md) | Exhaustive documentation of all 45+ environment variables, server credentials, and secrets. |
| [**Deployment Guide**](./DEPLOYMENT.md) | systemd user service setup, Docker Compose, Unraid/TrueNAS, lingering, and reverse proxies. |
| [**Feature Guides**](./docs/FEATURES.md) | Deep dives for Co-Watching, Two-Way Reconciliation, Content Bridge, Queue, and Notifications. |
| [**Architecture Blueprint**](./docs/ARCHITECTURE.md) | Complete component layers, data flows, and file-by-file directory manifest. |
| [**REST API Specification**](./docs/API.md) | Documentation for all 29 REST endpoints, webhook payloads, and Prometheus scrape metrics. |
| [**Troubleshooting & FAQ**](./docs/TROUBLESHOOTING.md) | Solutions for common errors (422, 409, 80%), health diagnostics, and networking setup. |

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
├── docs/                    # Static demo (GitHub Pages), Architecture, API, Features & FAQ
├── scripts/                 # Demo compilation & branding asset generators
└── tests/                   # Pytest test suite (171 unit & integration tests)
```

> 🏛️ For the complete architectural diagram, component layers, and detailed directory manifest, see [**`docs/ARCHITECTURE.md`**](./docs/ARCHITECTURE.md).

---

## 🗺️ Roadmap & Horizons

- [x] **Universal Media Server Ingestion**: Multi-server webhooks for Plex, Jellyfin, and Emby.
- [x] **Multi-Tracker Synchronization**: Simultaneous dispatch across Trakt, Simkl, AniList, and MyAnimeList.
- [x] **Bi-Directional Library Reconciliation**: Discrepancy diffing and played/rating sync across Plex, Jellyfin, Emby, Trakt, and Simkl.
- [x] **Watch Together / Co-Watch**: Dynamic multi-user partner scrobbling with show and device whitelists.
- [x] **Content Bridge**: Automated Trakt Watchlist sync to Sonarr and Radarr.
- [x] **Offline Queue & Resilience**: SQLite persistence with exponential backoff and transient failure retries.
- [x] **Dashboard Notification Configuration**: Live configuration of Discord, Telegram, Ntfy, and Pushover channels with per-channel test dispatch — no restarts required.
- [ ] **Direct P2P Sync**: Mesh synchronization between distributed Omniscrobble instances.
- [ ] **Dynamic Rules Engine**: In-app scrobble threshold and library filtering configuration editor.

> 📦 For release notes, changelogs, and upgrade instructions, visit [**GitHub Releases**](https://github.com/selits/omniscrobble/releases).

---

## 🔄 Updating / Upgrading

To update your installation to the latest release on your server or host:

```bash
./upgrade.sh
```

This automated script fetches the latest code from GitHub, updates dependencies in `.venv`, refreshes and re-enables the systemd user service, enables user lingering, and restarts the service cleanly.

---

## 🧪 Testing & Local Simulation

Omniscrobble includes a complete local testing framework featuring a CLI webhook simulator and a unified quality gate runner.

```bash
# 1. Run all quality gates (Pytest, Gitleaks, demo validation, git privacy audit)
./scripts/test_local.sh

# 2. Simulate media server webhooks locally without media playback
./scripts/simulate_webhook.py --server plex --scenario movie-finish --title "Inception" --year 2010

# 3. Run the unit test suite directly
.venv/bin/pytest -v
```

> 📖 For full webhook simulation options and scenario presets, see [**`docs/LOCAL_TESTING.md`**](./docs/LOCAL_TESTING.md).
