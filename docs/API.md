# Omniscrobble REST API Specification

This document provides the complete API reference for **Omniscrobble**, including HTTP methods, paths, authorization requirements, payload structures, and sample responses.

<p align="center">
  <a href="../README.md"><b>Overview</b></a> •
  <a href="../CONFIGURATION.md"><b>Configuration Guide</b></a> •
  <a href="../DEPLOYMENT.md"><b>Deployment Guide</b></a> •
  <a href="API.md"><b>API Reference</b></a> •
  <a href="https://selits.github.io/omniscrobble/"><b>Live Demo</b></a>
</p>

---

## 📑 Table of Contents

1. [Authentication & Security](#1-authentication--security)
2. [Web UI & Telemetry Endpoints](#2-web-ui--telemetry-endpoints)
3. [Media Server Webhook Ingestion](#3-media-server-webhook-ingestion)
4. [Admin Session & Authorization](#4-admin-session--authorization)
5. [Activity Feed, Playback & System Logs](#5-activity-feed-playback--system-logs)
6. [Manual Scrobbling & Trakt Watchlist](#6-manual-scrobbling--trakt-watchlist)
7. [Offline Queue & Disaster Recovery](#7-offline-queue--disaster-recovery)
8. [Watch Together & Multi-User Accounts](#8-watch-together--multi-user-accounts)
9. [Two-Way Media Server Reconciliation & Background Cloud Sync](#9-two-way-media-server-reconciliation--background-cloud-sync)
10. [Content Bridge (*Arr Automation)](#10-content-bridge-arr-automation)
11. [Multi-Tracker Hub (9 Trackers & Relays)](#11-multi-tracker-hub-9-trackers--relays)
12. [Cross-Tracker Reconciliation (Trakt ⇄ Simkl)](#12-cross-tracker-reconciliation-trakt--simkl)
13. [Runtime Settings Hub](#13-runtime-settings-hub)
14. [Synthetic Webhook Testing](#14-synthetic-webhook-testing)

---

## 1. Authentication & Security

Omniscrobble utilizes a multi-level access control model based on `WEBHOOK_SECRET`:

| Level | Description | How It Is Provided |
| --- | --- | --- |
| **Public** | Open read-only telemetry, PWA assets, and status feeds. | No credentials required. |
| **Webhook Secret** | Secures incoming media server webhooks from unauthorized external callers. | Query parameter `?token=<SECRET>` or HTTP Header `x-webhook-secret: <SECRET>`. |
| **Admin Authorization** | Required for state-mutating endpoints, configuration changes, backups, and live logs. | Cookie `admin_token=<SECRET>` (obtained via `/api/admin/unlock`), query param `?token=<SECRET>`, or header `x-webhook-secret: <SECRET>`. For cookie-authenticated mutating requests (`POST`, `DELETE`, `PUT`, `PATCH`), Double-Submit Cookie CSRF protection requires matching `X-CSRF-Token` header. |

> [!NOTE]
> If `WEBHOOK_SECRET` is not set in `.env`, the dashboard runs in local unrestricted mode and all endpoints grant administrative access automatically. Sessions utilize configurable `COOKIE_SAMESITE` policies (default: `lax`).

---

## 2. Web UI & Telemetry Endpoints

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/`** | `GET` | Public | **Main Web Dashboard**: Renders the responsive real-time dashboard UI. |
| **`/demo`** | `GET` | Public | **Air-Gapped Demo**: Renders the dashboard in isolated demo mode with mock data. |
| **`/health`** | `GET` | Public | **Healthcheck**: Returns JSON status, authentication state, and token health metrics. |
| **`/api/health/tokens`** | `GET` | Admin | **Proactive Token Health**: Evaluates expiration windows, renewal lifespans, and push alert triggers across Trakt, Simkl, MyAnimeList, and partner accounts. |
| **`/metrics`** | `GET` | Public | **Prometheus Metrics**: Exposes thread-safe telemetry in Prometheus text exposition format. |
| **`/manifest.json`** | `GET` | Public | **PWA Web App Manifest**: Metadata for mobile and desktop PWA installation. |
| **`/sw.js`** | `GET` | Public | **PWA Service Worker**: Version-locked background asset cache. |

---

## 3. Media Server Webhook Ingestion

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/webhook`** | `POST` | Webhook Secret | **Plex Ingestion**: Ingests Plex multipart/form-data or JSON payloads (`media.play`, `media.pause`, `media.resume`, `media.stop`, `media.scrobble`, `media.rate`, `library.new`). |
| **`/webhook/jellyfin`** | `POST` / `GET` | Webhook Secret | **Jellyfin Ingestion**: Ingests JSON notifications from the Jellyfin Webhook plugin. |
| **`/webhook/emby`** | `POST` / `GET` | Webhook Secret | **Emby Ingestion**: Ingests native Emby server webhooks. |
| **`/sonarr`** | `POST` | Webhook Secret | **Sonarr Download Ingestion**: Instantly adds newly imported downloads to your Trakt collection. |
| **`/radarr`** | `POST` | Webhook Secret | **Radarr Download Ingestion**: Instantly adds newly imported movies to your Trakt collection. |

---

## 4. Admin Session & Authorization

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/admin/unlock`** | `POST` | Rate-Limited | **Admin Unlock**: Validates `token` against `WEBHOOK_SECRET`. On success, issues secure HTTP-only `admin_token` and double-submit `csrf_token` cookies with configured `SameSite` policy. Protected by sliding-window brute-force rate limiting (5 failed attempts per 60s &rarr; HTTP 429 with `Retry-After`). Sanitizes security audit logs. |
| **`/api/admin/lock`** | `POST` | Public | **Admin Lock**: Deletes both `admin_token` and `csrf_token` session cookies, returning the UI to privacy-shielded mode. |
| **`/auth`** | `GET` | Public | **Trakt Authorization**: Starts Trakt OAuth device flow (supports `?user=username` for partner accounts). |
| **`/auth/simkl`** | `GET` | Admin | **Simkl Authorization**: Dedicated portal for Simkl OAuth device PIN activation. |
| **`/auth/anilist`** | `GET` | Admin | **AniList Authorization**: Web portal for AniList token entry. |
| **`/auth/mal`** | `GET` | Admin | **MyAnimeList Authorization**: Web portal for MyAnimeList token entry. |

---

## 5. Activity Feed, Playback & System Logs

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/events`** | `GET` | Public | **Recent Activity Feed**: Returns recent scrobbles, ratings, and Co-Watch sync statuses in JSON. Supports optional pagination (`?limit=10&offset=0`) and returns `{ events: [...], total: N }`. |
| **`/api/events/clear`** | `POST` | Admin | **Clear Activity Feed**: Purges the recent events history from disk (`data/events.json`). |
| **`/api/playback`** | `GET` | Public | **Active Playback**: Returns active streams, calculated progress, and recently finished media. |
| **`/api/stats/reset`** | `POST` | Admin | **Reset Stats**: Resets lifetime counters (movies, episodes, scrobbles, ratings) in `data/stats.json`. |
| **`/api/logs`** | `GET` | Admin | **System Log Viewer**: Reads live logs from `journalctl` (or ring buffer fallback) with automatic secret redaction. Supports `?level=ALL \| ERROR \| WARNING \| INFO` and `?limit=500`. |

---

## 6. Manual Scrobbling & Trakt Watchlist

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/search`** | `GET` | Admin | **Global Trakt Search**: Search movies and shows (`?query=...&type=movie \| show`). |
| **`/api/scrobble/manual`** | `POST` | Admin | **Manual History Scrobble**: Force-scrobble any movie or episode directly to Trakt, Simkl, AniList, or MAL with optional partner dual-sync. |
| **`/api/watchlist`** | `POST` | Admin | **Trakt Watchlist**: Add a movie or show to your personal Trakt watchlist. |
| **`/api/history/remove`** | `POST` | Admin | **Unscrobble Media**: Deletes a watched history entry from Trakt and secondary trackers with optional partner unlinking. |

---

## 7. Offline Queue & Disaster Recovery

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/queue`** | `GET` | Public | **Offline Queue Status**: Returns pending queue item count and database path. |
| **`/api/queue/retry`** | `POST` | Admin | **Flush Queue**: Immediately drains and retries all pending offline items against Trakt. |
| **`/api/queue/clear`** | `POST` | Admin | **Purge Queue**: Clears all pending and failed offline items from the SQLite database. |
| **`/api/backup`** | `GET` | Admin | **Download Backup**: Exports a timestamped archive containing all OAuth tokens, settings, and SQLite queue. Supports AES-256-GCM encryption via `?passphrase=`, `x-backup-passphrase` header, or `CONFIG_ENCRYPTION_KEY`. |
| **`/api/restore`** | `POST` | Admin | **Restore Backup**: Accepts a multipart archive upload with Zip Slip path-traversal protection and transparent AES-256-GCM decryption via form field `passphrase` or header. |

---

## 8. Watch Together & Multi-User Accounts

| Endpoint | Method | Auth | Description |
| --- | :--- | :--- | --- |
| **`/api/cowatch`** | `GET` | Public | **Co-Watch Status**: Returns linked partner profile, whitelist of shared shows, and allowed devices. |
| **`/api/cowatch/trackers`** | `GET` | Public | **Partner Cloud Trackers**: Returns connection and username status matrix for partner's secondary trackers (Trakt, Simkl, AniList, MAL). Supports `?demo=true`. |
| **`/api/cowatch/shows`** | `POST` | Admin | **Add Shared Show**: Adds a TV show title to `data/cowatch_shows.json`. |
| **`/api/cowatch/shows`** | `DELETE` | Admin | **Remove Shared Show**: Removes a show title from `data/cowatch_shows.json`. |
| **`/api/cowatch/settings`** | `POST` | Admin | **Toggle Co-Watch Settings**: Dynamically toggles movie dual-sync (`movies: true/false`). |
| **`/api/cowatch/sync`** | `POST` | Admin | **1-Click Partner Dual Sync**: Manually pushes a watched event to your partner's Trakt profile. |
| **`/api/sonarr/shows`** | `GET` | Admin | **Live Show Autocomplete**: Queries Sonarr for series titles, excluding already whitelisted shows. |

---

## 9. Two-Way Media Server Reconciliation & Background Cloud Sync

| Endpoint | Method | Auth | Description |
| --- | :--- | :--- | --- |
| **`/api/sync/status`** | `GET` | Public | **Reconciliation Telemetry**: Connection state, active media server, and last sync timestamp. |
| **`/api/sync/settings`** | `GET` | Public | **Reconciliation Settings**: Retrieves current reconciliation server configuration, masked tokens, sync interval, and ratings sync toggles. |
| **`/api/sync/settings`** | `POST` | Admin | **Update Reconciliation Settings**: Updates media server URLs, tokens, user IDs, sync interval, and trigger startup flags with immediate background engine reload. |
| **`/api/sync/diff`** | `GET` | Admin | **Scan Discrepancies**: Compares Trakt watched history against media server library sections. |
| **`/api/sync/reconcile`** | `POST` | Admin | **Execute Reconciliation**: Triggers background batch reconciliation for selected or all items. Mutex-protected (returns `HTTP 409 Conflict` if a sync is running). |
| **`/api/sync/progress`** | `GET` | Public | **Sync Progress**: Returns live percentage and progress counts for active sync batches. |
| **`/api/sync/test-connection`** | `POST` | Admin | **Test Media Server**: Validates connectivity and credentials for Plex, Jellyfin, or Emby. |
| **`/api/sync/background/status`** | `GET` | Public | **Background Cloud Sync Telemetry**: Returns automated background cloud sync status from `data/sync_state.json` (last run status, items reconciled, next scheduled run, task breakdowns). Supports `?demo=true`. |
| **`/api/sync/background/run`** | `POST` | Admin | **Trigger Cloud Sync Now**: Manually executes a full background synchronization and Letterboxd CSV export cycle. Mutex-protected (returns `HTTP 409 Conflict` if a sync is running). Supports `?demo=true`. |

---

## 10. Content Bridge (*Arr Automation)

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/arr/status`** | `GET` | Public | **Content Bridge Status**: Connection health, latency, and library totals for Sonarr and Radarr. |
| **`/api/arr/sync`** | `POST` | Admin | **Sync Watchlist Now**: Scans Trakt watchlist and sends missing media to Sonarr/Radarr. |
| **`/api/arr/test-connection`** | `POST` | Admin | **Test *Arr Connection**: Tests connectivity to Sonarr or Radarr URL and API key. |
| **`/api/ecosystem`** | `GET` | Public | **Ecosystem Status**: Aggregated health status (9/9 services) across servers, trackers, and downloaders. |

---

## 11. Multi-Tracker Hub (9 Trackers & Relays)

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/trackers/status`** | `GET` | Public | **9-Tracker Status Matrix**: Aggregated connection, token, and health telemetry across all connected trackers (Trakt, Simkl, AniList, MAL, TMDb, Letterboxd, Kitsu, BetaSeries, Serializd, MDBList). |
| **`/api/trakt/status`** | `GET` | Public | **Trakt Status**: Connection status, token health, and authenticated username. Supports `?user=username`. |
| **`/api/trakt/disconnect`** | `POST` | Admin | **Disconnect Trakt**: Purges local Trakt OAuth tokens (`?user=username` for partner). |
| **`/api/simkl/status`** | `GET` | Public | **Simkl Status**: Connection health and authenticated username. Supports `?user=username`. |
| **`/api/simkl/pin`** | `POST` | Admin | **Request Device PIN**: Generates an OAuth device PIN for headless browser activation. Supports `?user=username` to target secondary partner profile. |
| **`/api/simkl/poll`** | `POST` | Admin | **Poll Device PIN**: Polls Simkl for user authorization confirmation. Accepts `{"user_code": "...", "user": "username"}` to persist to partner token profile. |
| **`/api/simkl/disconnect`** | `POST` | Admin | **Disconnect Simkl**: Purges local Simkl OAuth credentials. Supports `?user=username`. |
| **`/api/anilist/status`** | `GET` | Public | **AniList Status**: Connection status and user profile. Supports `?user=username`. |
| **`/api/anilist/token`** | `POST` | Admin | **Save AniList Token**: Saves user access token for GraphQL scrobbling. Accepts `{"token": "...", "user": "username"}` to persist to partner token profile. |
| **`/api/anilist/disconnect`** | `POST` | Admin | **Disconnect AniList**: Deletes local AniList token. Supports `?user=username`. |
| **`/api/mal/status`** | `GET` | Public | **MyAnimeList Status**: Connection status and user profile. Supports `?user=username`. |
| **`/api/mal/token`** | `POST` | Admin | **Save MAL Token**: Saves access token for MyAnimeList REST v2 API. Accepts `{"token": "...", "user": "username"}` to persist to partner token profile. |
| **`/api/mal/disconnect`** | `POST` | Admin | **Disconnect MAL**: Deletes local MyAnimeList token. Supports `?user=username`. |
| **`/api/letterboxd/export`** | `GET` | Admin | **Letterboxd Diary CSV Export**: Generates and downloads an RFC-4180 Letterboxd-compliant CSV diary export of watched movies. |
| **`/api/relay/status`** | `GET` | Public | **Mobile Relay Status**: Connection health for mobile Trakt relays (SeriesGuide, Showly). |
| **`/api/anime/resolve`** | `GET` | Public | **Anime Resolver**: Resolves AniList GraphQL / MAL ID mapping for an anime title. |

---

## 12. Cross-Tracker Reconciliation (Trakt ⇄ Simkl)

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/cross-sync/status`** | `GET` | Public | **Cross-Sync Status**: Reconciliation telemetry between Trakt and Simkl. |
| **`/api/cross-sync/diff`** | `GET` | Admin | **Cross-Tracker Diff**: Scans and returns history/rating discrepancies between Trakt and Simkl. |
| **`/api/cross-sync/scan`** | `POST` | Admin | **Scan Libraries**: Triggers on-demand comparison across Movies, Shows, and Anime. |
| **`/api/cross-sync/execute`** | `POST` | Admin | **Execute Cross-Sync**: Syncs discrepancies bi-directionally between Trakt and Simkl. |
| **`/api/cross-sync/progress`** | `GET` | Public | **Cross-Sync Progress**: Real-time progress percentage for active cross-sync jobs. |

---

## 13. Runtime Settings Hub

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/settings`** | `GET` | Public | **Get Runtime Settings**: Retrieves current media server listener toggles, tracker active states, masked credentials, rules, and notifications configuration. |
| **`/api/settings`** | `POST` | Admin | **Update Runtime Settings**: Updates media server listeners, tracker states, credentials, reconciliation, arr, notifications, and rules. |
| **`/api/settings/rules`** | `GET` | Public | **Get Scrobble Rules**: Retrieves current scrobble rules (granular episode & movie thresholds, min duration, episode duration toggle, ignored libraries, path regex patterns). |
| **`/api/settings/rules`** | `POST` | Admin | **Update Scrobble Rules**: Updates scrobble thresholds, minimum playback duration, ignored libraries, and regex patterns with automatic clamping and validation. |
| **`/api/settings/toggle`** | `POST` | Admin | **Toggle Service**: Enables/disables media server listeners or pauses/resumes trackers. |
| **`/api/settings/save-all`** | `POST` | Admin | **Save Settings Hub**: Atomically saves all Settings Hub tabs (servers, trackers, rules, notifications) to `data/settings.json` without server restarts. |
| **`/api/notifications/test`** | `POST` | Admin | **Test Notification Channel**: Dispatches a test message to a specified channel (`discord`, `telegram`, `ntfy`, `pushover`). Body: `{"channel": "discord"}`. Returns `{"ok": true}` on success. |

---

## 14. Synthetic Webhook Testing

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/test/webhook`** | `POST` | Admin | **Webhook Simulator**: Simulates playback events (`media.play`, `media.pause`, `media.scrobble`), tests filter rules, inspects Co-Watch eligibility reasons, and optionally dispatches live to Trakt and partner. |
