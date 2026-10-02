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
  <a href="#-features"><b>Features</b></a> •
  <a href="#-step-by-step-setup-guide"><b>Quickstart</b></a> •
  <a href="CONFIGURATION.md"><b>Configuration Guide</b></a> •
  <a href="DEPLOYMENT.md"><b>Deployment Guide</b></a> •
  <a href="docs/API.md"><b>API Reference</b></a> •
  <a href="https://selits.github.io/omniscrobble/"><b>Live Demo</b></a>
</p>

A lightweight, high-performance Python service that receives media server webhooks (Plex, Jellyfin, and Emby) and automatically tracks your TV shows, movies, and anime across Trakt, Simkl, AniList, and MyAnimeList—updating playback status in real-time, syncing ratings and collections, and keeping your watch history synchronized everywhere.

---

## 🌟 Features

- **Control Dashboard & Media Server Toggles**: 1-click runtime toggles to enable/disable media server webhooks (Plex, Jellyfin, Emby) and pause/resume individual tracker dispatches (Trakt, Simkl, AniList, MAL) directly from the UI with zero restarts. All server listeners are disabled by default on clean installations for an opt-in, privacy-first security posture.
- **Quick-Mark Watched & Multi-Tracker Dispatch**: Mark any movie or episode as watched on demand via an interactive modal featuring live global catalog search or direct manual entry (with custom season/episode selectors), granular tracker checkboxes, and automatic Co-Watch partner dual-sync.
- **1-Click Unscrobble & History Deletion**: Easily remove incorrectly tracked plays directly from the Recent Activity table (`POST /api/history/remove`) with optional partner un-scrobble support.
- **Persistent Playback Stats & Activity History**: Playback counters (movies, episodes, scrobbles, ratings) and recent activity feed entries are persisted to disk (`data/stats.json`, `data/events.json`) and survive service restarts, software upgrades, and system backups.
- **Tracker Failure Telemetry & Smart Cooldown Alerts**: Dispatches actionable alert notifications when third-party tracker APIs encounter outages or rate limits, protected by an intelligent 30-minute deduplication cache to eliminate alert spam.
- **Anime Tracking Engine (AniList & MyAnimeList)**: Dedicated anime identification heuristics, title normalization, and 4-way multi-tracker dispatch across Trakt, Simkl, **AniList (GraphQL API)**, and **MyAnimeList (REST API v2)**. Automatically detects anime series, extracts AniList/MAL IDs from GUIDs, maps titles via AniList GraphQL, caches results locally (`data/anime_cache.json`) with negative caching for zero-latency lookups, and scrobbles episode progress and ratings in real-time.
- **Multi-Tracker Architecture & Simkl Dual-Tracking**: Broadcast playback scrobbles (start, pause, stop) and ratings across both Trakt and **Simkl** simultaneously. Supports Movies, TV Shows, and Anime with decoupled zero-latency background task dispatch and OAuth Device PIN browser activation (`/auth/simkl`).
- **Cross-Tracker Watched History Importer & Two-Way Sync**: Full bi-directional library reconciliation and bulk synchronization between **Trakt.tv** and **Simkl.com** (`/api/cross-sync/*`). Compare full libraries and user ratings across Movies, TV Shows, and Anime, review discrepancies in an interactive modal with direction and type filters, and execute 1-click selective or complete sync with real-time progress bars.
- **Content Bridge & *Arr Automation**: Connect your Trakt Watchlist (`/sync/watchlist`) directly to Sonarr and Radarr for automated media acquisition, intelligent deduplication against existing libraries, automated root folder/quality profile discovery, and immediate download searches.
- **Multi-Server Ecosystem Dashboard**: Real-time multi-service observability widget monitoring live connectivity, library counts, latency, and operational health across Plex, Jellyfin, Emby, Trakt, Simkl, AniList, MyAnimeList, Sonarr, and Radarr (9/9 services).
- **Two-Way Synchronization & Multi-Server Reconciliation**: Bi-directional matching of watched history and ratings between media servers (**Plex**, **Jellyfin**, **Emby**) and Trakt (`/api/sync/*`), multi-server discrepancy diff table with selective sync tabs, server-specific credential configuration, automated periodic background sync, and smart TTL-based scrobble loop prevention.
- **Universal Webhook Ingestion**: Native webhook support for **Plex** (`/webhook`), **Jellyfin** (`/webhook/jellyfin`), and **Emby** (`/webhook/emby`), standardizing metadata, provider IDs (IMDb, TMDb, TVDb), and playback states into a unified scrobble pipeline.
- **Granular Scrobble Thresholds**: Configurable media-specific thresholds — set `EPISODE_SCROBBLE_THRESHOLD=80` for TV episodes (allowing credit skipping) and `MOVIE_SCROBBLE_THRESHOLD=90` for feature films (preventing premature scrobbles during climaxes).
- **Mobile Progressive Web App (PWA) & OLED Theme**: Fully installable PWA with offline service worker caching, dynamic SVG app icons, and an instant True-Black OLED dark mode toggle (`🌙 OLED`).
- **Automatic Show & Movie Tracking**: Synchronizes playback in real-time (`media.play`, `media.pause`, `media.stop`) and automatically marks episodes as viewed in Trakt history upon completion (`media.scrobble`).
- **Instant Rating Synchronization**: Automatically syncs star and 1–10 numerical ratings set in Plex, Jellyfin, or Emby (`media.rate`) directly to your Trakt profile for movies, episodes, and entire shows (`/sync/ratings`).
- **Trakt Collection Synchronization**: Automatically syncs newly downloaded or added movies and episodes to your Trakt collection (`library.new` $\to$ `/sync/collection`), recording technical media specifications (resolution, audio codec, audio channels).
- **Library Section Filtering**: Exclude home video, fitness, or personal libraries (`EXCLUDED_LIBRARIES`) or whitelist specific libraries (`ALLOWED_LIBRARIES`) so private files never pollute your Trakt profile.
- **Modern GUID Resolution**: Supports modern metadata agents (`imdb://`, `tmdb://`, `tvdb://`), TV show year matching for remake disambiguation, and fallback title matching.
- **Robust Multipart & JSON Parsing**: Handles Plex's multipart/form-data payloads as well as Jellyfin and Emby JSON payloads without validation errors.
- **Smart Pause Handling**: Automatically finalizes scrobbles if playback is paused past the completion threshold, preventing Trakt API 422 warnings.
- **Web UI & Device Code OAuth Flow**: Authorize directly in your browser via `/auth` or headlessly via terminal (`python auth.py`) using Trakt's official activation code (`https://trakt.tv/activate`).
- **Resilient Async Trakt Client**: Built on non-blocking `httpx.AsyncClient` with automatic OAuth token refresh on 401, token health telemetry, and exponential backoff on 429 rate limits.
- **Persistent Offline Queue & Retry Worker**: Automatically preserves scrobbles, watches, ratings, and collection additions in a local SQLite database during Trakt API downtime or network outages, retrying in the background until successfully synced.
- **Multi-Channel Push Notifications**: Delivers real-time rich embeds to Discord, messages to Telegram, and lightweight push alerts to Ntfy or Pushover upon scrobbles, ratings, and collection additions.
- **Homelab Observability & Prometheus Metrics**: Built-in `/metrics` endpoint exporting standard Prometheus exposition metrics (request counts, scrobble status, queue depth, active playback sessions, uptime) for Grafana monitoring.
- **1-Click System Backup & Restore**: Export and restore a timestamped `.zip` archive containing your OAuth tokens, SQLite retry database, and co-watch settings directly from the dashboard.
- **Live Playback Observability & Remaining Time**: Real-time animated dashboard card showing active streams (`▶ Currently Streaming` / `⏸ Paused`), client-side clock drift interpolation, dynamic time remaining (`"24m left"`, `"24m left (paused)"`), progress bar, device names, and recently finished media.
- **Integrated Manual Scrobble & Trakt Watchlist**: Search Trakt's global catalog directly from the dashboard to mark any missed movie or episode as watched (`POST /api/scrobble/manual`) or bookmark upcoming titles to your Trakt watchlist (`POST /api/watchlist`) with 1 click.
- **Multi-User Trakt Support**: Link separate Trakt accounts for different media server users (`/auth?user=username`), allowing household members sharing the server to scrobble to their own profiles.
- **Watch Together (Co-Watching) Engine**: Automatically dual-scrobbles watched TV shows or movies to your partner's Trakt account when you watch together, while leaving solo shows untracked. Manage shared shows directly from your phone or desktop with interactive tag chips, dynamic movie toggle, automatic `.env` merging, and live `👥 Co-Watched` / `👥 Solo` activity badges.
- **Interactive Synthetic Webhook Testing**: Built-in modal and endpoint (`POST /api/test/webhook`) to simulate playback events (playing, paused, scrobble), verify filter rules, inspect co-watching eligibility reasons, and optionally execute live Trakt history and partner dual-sync.
- **Mobile-First Responsive Web Design**: Fully responsive layout designed for all screen sizes (desktop, tablet, and mobile devices like Android and iOS phones) with fluid grids, touch-friendly scrolling, and compact modal dialogs.
- **Sonarr & Radarr Integrations**: Real-time autocomplete for TV series from your Sonarr library (automatically excluding shows already in your whitelist) plus direct webhook endpoints (`/sonarr`, `/radarr`) for instant Trakt collection sync upon download import.
- **Interactive Air-Gapped Demo & Live GitHub Pages Preview**: Explore a fully populated, authenticated dashboard view with realistic mock playback (*Severance S02E01* on Apple TV 4K), 1,428 scrobbles, activity history, linked multi-user accounts, and Co-Watch configuration with zero risk to disk storage or Trakt credentials. Test it locally via `/demo` or try the zero-install live demo hosted on [GitHub Pages](https://selits.github.io/omniscrobble/).
- **Authenticated System Log Viewer**: Inspect live service logs directly from the dashboard via an interactive terminal modal with level filtering (`ALL`, `ERROR`, `WARNING`, `INFO`), keyword search, auto-scroll, and copy-to-clipboard, backed by `journalctl` on Linux systemd and an in-memory ring buffer fallback with automatic secret redaction.
- **Dashboard Admin Security & Screenshot Privacy Shield**: Public visitors see a hardened, privacy-shielded view (masked usernames, completely masked partner account `@●●●●●●●●`, masked server hostname/port `http://●●●●●●●●:●●●●/webhook?token=●●●●●●●●` for safe screenshots, and locked administrative endpoints). Unlock full administrative access and 1-click URL copying anytime with your Webhook Secret.
- **User Whitelist**: Easily limit scrobbling to your specific username so other family members/friends sharing your server don't overwrite your Trakt history.
- **Live Streamlined Dashboard**: Access `http://<server-ip>:<PORT>/` to view Trakt connection health, server uptime, scrobble statistics, multi-server webhook URL tabs, recent activity logs with optional 30s auto-refresh, and a repository footer with live version badge.
- **Docker & Remote Server Ready**: Tested and optimized for containerized, VPS, and remote Linux environments with non-root security, healthchecks, and `env_file` auto-loading.

---

## 🚦 Integration & Verification Matrix

Omniscrobble is developed and maintained in an active daily homelab environment. Because homelab setups vary, the matrix below details which integrations and capabilities are **verified in live production** by the maintainer versus those that are **implemented and unit-tested to specification but awaiting community validation**.

### 🎬 Media Servers

| Platform | Webhook Ingestion | Rating Sync | Collection Sync | Two-Way Reconciliation | Status |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **Plex** | ✅ Verified | ✅ Verified | ✅ Verified | ✅ Verified | **Production Tested** (Maintainer daily driver) |
| **Jellyfin** | 🧪 Unit-tested | 🧪 Unit-tested | 🧪 Unit-tested | 🧪 Unit-tested | **Community Beta** (Untested in production by maintainer) |
| **Emby** | 🧪 Unit-tested | 🧪 Unit-tested | 🧪 Unit-tested | 🧪 Unit-tested | **Community Beta** (Untested in production by maintainer) |

### 🎯 Trackers & Sync Engines

| Tracker / Service | Playback Scrobble | Rating Sync | Library Reconciliation | Cross-Tracker Sync | Status |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **Trakt.tv** | ✅ Verified | ✅ Verified | ✅ Verified | ✅ Verified | **Production Tested** (Primary tracker) |
| **Simkl** | ✅ Verified | ✅ Verified | N/A | ✅ Verified | **Production Tested** (Simultaneous dual-tracker) |
| **AniList** | 🧪 Unit-tested | 🧪 Unit-tested | N/A | N/A | **Community Beta** (GraphQL anime tracking) |
| **MyAnimeList (MAL)** | 🧪 Unit-tested | 🧪 Unit-tested | N/A | N/A | **Community Beta** (REST v2 anime tracking) |

### ⚡ Acquisition & Automation

| Service | Feature | Status | Notes |
| :--- | :--- | :---: | :--- |
| **Sonarr** | Download Webhook $\to$ Trakt Collection | ✅ Verified | Instant collection update on series import |
| **Sonarr** | Co-Watch Live Series Autocomplete | ✅ Verified | Queries active library to suggest shows |
| **Sonarr** | Trakt Watchlist Auto-Acquisition | ✅ Verified | Automatically grabs newly watchlisted series |
| **Radarr** | Download Webhook $\to$ Trakt Collection | ✅ Verified | Instant collection update on movie import |
| **Radarr** | Trakt Watchlist Auto-Acquisition | ✅ Verified | Automatically grabs newly watchlisted movies |

> [!TIP]
> **Community Feedback Welcomed**: If you run Jellyfin, Emby, AniList, or MyAnimeList with Omniscrobble, please report your experience or open an issue on GitHub! Direct API integrations adhere to official upstream API schemas and 100% of unit tests pass, but your real-world homelab feedback helps verify and refine them.

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

## 🔗 Adding Webhooks to Media Servers

### Plex Media Server

1. Open **Plex Web** (`https://app.plex.tv/desktop`).
2. Go to **Settings (wrench icon) &rarr; Webhooks** (under Account settings).
3. Click **Add Webhook** and enter your endpoint:
   - Without Secret: `http://<your-server-ip-or-domain>:<PORT>/webhook`
   - With Secret: `http://<your-server-ip-or-domain>:<PORT>/webhook?token=YOUR_WEBHOOK_SECRET`
4. Click **Save Changes**.

### Jellyfin Media Server

1. In your Jellyfin Server Dashboard, navigate to **Plugins &rarr; Catalog**.
2. Find and install the official **Webhook** plugin, then restart Jellyfin.
3. Open **Dashboard &rarr; Plugins &rarr; Webhook**, and click **Add Generic Webhook**.
4. Configure the webhook destination:
   - **Webhook URL**: `http://<your-server-ip-or-domain>:<PORT>/webhook/jellyfin` (or `?token=YOUR_WEBHOOK_SECRET`)
   - **Notification Type**: Enable `Playback Start`, `Playback Progress`, `Playback Stop`, and `User Data Saved`.
5. Click **Save**.

### Emby Media Server

1. In your Emby Server Dashboard, navigate to **Settings &rarr; Webhooks**.
2. Click **Add Webhook** and select **Generic Webhook**.
3. Configure the webhook destination:
   - **Webhook URL**: `http://<your-server-ip-or-domain>:<PORT>/webhook/emby` (or `?token=YOUR_WEBHOOK_SECRET`)
   - **Events**: Enable `playback.start`, `playback.pause`, `playback.unpause`, `playback.stop`, `item.rate`, and `item.markfavorite`.
4. Click **Save**.

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

### 1. The Co-Watching Dilemma

When couples, roommates, or families watch TV shows together on a shared living room Plex profile, only the primary profile's Trakt account traditionally gets updated. If you try to scrobble everything, your partner's Trakt account gets polluted with shows you watched alone.

### 2. The Solution: Intelligent Dual-Sync

**Omniscrobble** solves this with an integrated **Watch Together Engine**:

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
   - In Sonarr: Go to **Settings &rarr; Connect &rarr; Add Webhook**.
   - URL: `http://<server>:<PORT>/sonarr` (or `http://<server>:<PORT>/sonarr?token=YOUR_SECRET` if `WEBHOOK_SECRET` is set).
   - Triggers: Check **On Download** and **On Upgrade**.
   - Click **Test** and **Save**. Your Trakt collection will now update the moment Sonarr imports a download!
   - (Radarr is also supported using `http://<server>:<PORT>/radarr`).

---

## 🔄 Two-Way Synchronization & Multi-Server Library Reconciliation

Standard scrobbling is one-directional (Media Server $\to$ Trakt). When you watch a movie in theaters, on Netflix, on an airplane, or via a mobile app, you mark it as watched on Trakt—leaving your local media server library showing it as "Unwatched".

Omniscrobble features a bi-directional reconciliation engine that bridges your local media server libraries (**Plex**, **Jellyfin**, and **Emby**) with your Trakt cloud history:

> [!NOTE]
> **Media Server Support Status**:
> - **Plex**: Fully tested, validated, and used daily in active production.
> - **Jellyfin & Emby**: Direct API connection, library scanning, watched status toggling, and rating updates are implemented in strict accordance with official MediaBrowser REST API specifications and backed by comprehensive unit tests. However, because the maintainer's personal homelab environment is Plex-only, Jellyfin and Emby direct API integrations and reconciliation are currently considered **Community Beta / Untested in Production**. Real-world feedback, verification, and bug reports from Jellyfin and Emby users are warmly welcomed!

### 1. How Reconciliation Works

- **Direct Media Server APIs**: Connects securely via direct REST APIs to Plex (`PlexApiClient`), Jellyfin (`JellyfinApiClient`), and Emby (`EmbyApiClient`). Credentials can be set in `.env` or configured and tested directly within the dashboard UI via the **⚙️ Server Settings** modal.
- **Intelligent GUID & Provider ID Matching**: Compares Trakt watched history (`/sync/watched/movies`, `/sync/watched/shows`) against your media server library sections, resolving titles accurately using IMDb (`imdb://tt...`), TMDb (`tmdb://...`), and TVDb identifiers across all supported server formats.
- **Discrepancy Categorization**:
  - `Trakt Only`: Watched in Trakt cloud history, but marked unwatched on your media server.
  - `Server Only`: Watched on your media server, but missing from Trakt history.
  - `Rating Mismatch`: Rated on both platforms but with different values (e.g. 8/10 on media server vs. 9/10 on Trakt).

### 2. Smart Loop Prevention Architecture

When Omniscrobble calls your media server to mark an item as watched or set a rating, media servers normally fire an outgoing webhook. Without safeguards, this would create an infinite scrobble ping-pong loop.

Omniscrobble features a built-in, thread-safe `LoopPreventionManager`:

- Tracks synchronized media IDs, rating keys, and provider GUIDs in an in-memory cache with an automated TTL (default 60s).
- Webhooks matching recently synchronized items from Plex, Jellyfin, or Emby are dropped immediately before entering the scrobble pipeline.
- Cleans expired cache entries automatically to ensure subsequent organic plays are scrobbled normally.

### 3. Interactive Web Dashboard UI

The web dashboard includes a dedicated **Library Reconciliation** card and interactive multi-server modal:

- **Server Switcher Tabs**: Seamlessly switch discrepancy views and actions between **🎬 Plex**, **🟣 Jellyfin**, and **🟢 Emby**.
- **1-Click Scan & Connection Diagnostics**: View real-time discrepancy counts, latency, and connection status for each media server.
- **Interactive Diff Modal (`#reconcile-modal`)**: Filter discrepancies by category (`All`, `Trakt Only`, `Server Only`, `Rating Mismatch`).
- **Selective Syncing**: Select specific items via checkboxes or click **"Sync All"** / **"Quick Reconcile (Trakt ➔ Server)"**.
- **Live Progress Bar**: Polled in real-time (`GET /api/sync/progress`) showing batch progress, percentage, and success/failure tallies.
- **Interactive Credentials Manager (`#reconcile-settings-modal`)**: Configure server URLs, tokens, and user IDs with live connection test buttons directly from the dashboard.

### 4. Reverse Sync Configuration

Configure your media server connection in `.env` or dynamically in the **⚙️ Settings Hub** on the web dashboard:

```ini
# Direct Plex Media Server Connection (Example)
PLEX_URL=http://<your-server-ip-or-domain>:32400
PLEX_TOKEN=your_plex_token_here
```

> 💡 **Need help getting your server URL, `X-Plex-Token`, or Jellyfin/Emby User ID?**  
> See the [**Plex Credentials Guide**](./CONFIGURATION.md#plex-media-server) or [**Jellyfin & Emby Guides**](./CONFIGURATION.md#jellyfin-media-server) in `CONFIGURATION.md` for fast, visual walkthroughs.

---

## 📥 Content Bridge & *Arr Automation

Manually finding movies and TV shows across your media download stack is tedious. **Omniscrobble v1.6.0** introduces a high-performance **Content Bridge** that directly connects your personal Trakt Watchlist (`/sync/watchlist/movies`, `/sync/watchlist/shows`) to **Radarr** and **Sonarr**.

### 1. How the Content Bridge Works

- **Trakt Watchlist Ingestion**: Scans your authenticated Trakt account's watchlist for newly bookmarked movies and TV series.
- **Intelligent Metadata Lookup**:
  - For movies, queries Radarr's lookup API using TMDb IDs (`/api/v3/movie/lookup?term=tmdb:...`).
  - For TV shows, queries Sonarr's lookup API using TVDb IDs (`/api/v3/series/lookup?term=tvdb:...`).
- **Deduplication Engine**: Checks whether the item already exists in your Radarr or Sonarr library before submitting an addition, preventing duplicate requests and unnecessary processing.
- **Auto Root Folder & Quality Profile Resolution**:
  - Automatically selects configured defaults (`RADARR_ROOT_FOLDER`, `SONARR_ROOT_FOLDER`, `RADARR_QUALITY_PROFILE_ID`, `SONARR_QUALITY_PROFILE_ID`).
  - If unset, automatically queries the active instance for available root folders and profiles and picks the first valid storage location.
- **Immediate Acquisition Search**: Adds the media with `monitored: true` and triggers immediate indexer search when `SEARCH_ON_ADD=true`.
- **Multi-Channel Dispatch**: Broadcasts rich addition alerts to Discord, Telegram, Ntfy, or Pushover with custom styling and direct Trakt links.

### 2. Multi-Server Ecosystem Dashboard

The web dashboard features an interactive **Multi-Server Ecosystem** widget (`GET /api/ecosystem`) and **Content Bridge** status card:

- **Live Server Health**: Instant status indicator dots (Online / Offline), version reporting, and round-trip latency metrics for Plex, Jellyfin, Emby, Trakt, Sonarr, and Radarr.
- **Dedicated Watchlist Sync Modal (`#arr-modal`)**: View server connection statuses, root storage paths, active quality profiles, and a 1-click **"Sync Watchlist Now"** button.
- **Background Periodic Worker**: Automatically polls and syncs your Trakt watchlist at a configurable interval (`ARR_WATCHLIST_INTERVAL`, default 3600 seconds; set to `0` to disable background worker).

### 3. Content Bridge Configuration

Enable watchlist automation and acquisition search in your `.env` (or configure dynamically in the dashboard):

```ini
AUTO_ADD_FROM_WATCHLIST=true
SEARCH_ON_ADD=true
ARR_WATCHLIST_INTERVAL=3600
```

> 💡 **Looking for Sonarr/Radarr API keys, Quality Profile IDs, or Root Folder setup?**  
> Check the [**Acquisition Stack (*Arr Automation) Guide**](./CONFIGURATION.md#3-acquisition-stack-arr-automation) in `CONFIGURATION.md`.

---

## 📊 Homelab Observability & Prometheus Metrics

**Omniscrobble** includes a built-in, thread-safe Prometheus metrics registry exporting directly on `/metrics` (enabled via `PROMETHEUS_METRICS_ENABLED=true`).

### Prometheus Scrape Configuration

Add the following to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'omniscrobble'
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
- `NOTIFY_ON_RATE=true`: Alerts when rating media in Plex, Jellyfin, or Emby.
- `NOTIFY_ON_COLLECTION=true`: Alerts when new media is added to your collection.

> 📖 **Channel Setup Guides**: For step-by-step instructions on generating Discord webhooks, Telegram bots (`@BotFather`), Ntfy topics, and Pushover keys, see [**Multi-Channel Notification Setup**](./CONFIGURATION.md#6-multi-channel-notification-setup) in `CONFIGURATION.md`.

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

**Omniscrobble** follows a clean, modular Python package architecture:

```text
omniscrobble/
├── app/
│   ├── clients/             # External API integrations
│   │   ├── trakt_client.py  # Trakt OAuth device flow, scrobbling, ratings, collection sync & search
│   │   ├── simkl_client.py  # Simkl REST API client for OAuth Device PIN flow, dual-scrobbling & ratings
│   │   ├── anilist_client.py # AniList GraphQL API client for anime tracking & user lists
│   │   ├── mal_client.py    # MyAnimeList REST API v2 client for anime progress & rating sync
│   │   ├── plex_api_client.py # Direct Plex Media Server REST API client for watch status & ratings
│   │   ├── mediabrowser_api_client.py # Base client for MediaBrowser-compatible REST APIs
│   │   ├── jellyfin_api_client.py # Direct Jellyfin Media Server REST API client
│   │   ├── emby_api_client.py # Direct Emby Media Server REST API client
│   │   ├── sonarr_client.py # Sonarr REST API client for TV series & download imports
│   │   └── radarr_client.py # Radarr REST API client for movies, quality profiles & root folders
│   ├── services/            # Core business logic services
│   │   ├── anime_resolver.py   # Anime detection heuristics, title normalization & GUID caching
│   │   ├── arr_bridge.py       # Content Bridge manager for Trakt watchlist sync & ecosystem health
│   │   ├── cowatch_manager.py  # Watch Together whitelist & dual-scrobble rules engine
│   │   ├── cross_tracker_sync.py # Trakt <-> Simkl reconciliation & bi-directional sync engine
│   │   ├── demo_manager.py     # Air-gapped mock playback, stats, and activity generator
│   │   ├── log_manager.py      # Systemd journalctl reader, in-memory ring buffer & secret redaction
│   │   ├── loop_prevention.py  # Thread-safe TTL cache for echo loop suppression
│   │   ├── multi_tracker.py    # Multi-tracker coordinator for dual-dispatch scrobbling and rating sync
│   │   ├── notifier.py         # Multi-channel notifications (Discord, Telegram, Ntfy, Pushover)
│   │   ├── playback_manager.py # Active streaming sessions & dashboard cards
│   │   ├── queue_manager.py    # Persistent SQLite offline retry queue & background worker
│   │   ├── reverse_sync_manager.py # Bi-directional library reconciliation & reverse sync engine
│   │   ├── settings_manager.py # Persistent runtime media server listeners & tracker pause toggles
│   │   └── user_manager.py     # Multi-user account client cache & token persistence
│   ├── templates/           # Clean, externalized HTML/CSS/JS dashboard and auth views
│   │   ├── dashboard.html   # Main real-time status dashboard view
│   │   ├── auth.html        # Trakt device activation view
│   │   ├── auth_simkl.html  # Simkl OAuth device PIN activation view
│   │   ├── auth_anilist.html # AniList access token authorization view
│   │   ├── auth_mal.html    # MyAnimeList access token authorization view
│   │   └── auth_locked.html # Admin authorization gate view
│   ├── config.py            # Centralized environment & directory configuration
│   ├── main.py              # FastAPI application, route handlers & template rendering
│   ├── metrics.py           # Thread-safe Prometheus metrics registry & exposition formatter
│   ├── plex_parser.py       # Plex multipart/JSON webhook parsing & media models
│   ├── jellyfin_parser.py   # Jellyfin webhook parsing & provider ID translation
│   └── emby_parser.py       # Emby server webhook parsing & provider ID translation
├── docs/                    # GitHub Pages static interactive demo deployment
│   ├── index.html           # Standalone dashboard demo with client-side API simulator
│   └── .nojekyll            # Bypass Jekyll processing on GitHub Pages
├── scripts/                 # Maintenance and build utilities
│   ├── generate_static_demo.py # Compiles dashboard template & mock datasets into static demo
│   └── generate_logo_assets.py # Renders branding, banner, and social card graphics
├── main.py                  # Backward-compatible service entrypoint (Uvicorn)
├── auth.py                  # Standalone CLI device code authentication tool
├── plex-trakt.service       # systemd user service unit
├── start.sh                 # Portable startup wrapper script
├── upgrade.sh               # 1-click automated upgrade script
├── Dockerfile               # Multi-stage hardened non-root container image
└── tests/                   # Comprehensive pytest test suite (160 tests)
```

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
