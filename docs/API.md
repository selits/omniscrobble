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
6. [Manual Scrobbling, Standalone Players & Trakt Watchlist](#6-manual-scrobbling-standalone-players--trakt-watchlist)
7. [Offline Queue & Disaster Recovery](#7-offline-queue--disaster-recovery)
8. [Watch Together & Household Multi-Tenancy](#8-watch-together--household-multi-tenancy)
9. [Two-Way Media Server Reconciliation & Background Cloud Sync](#9-two-way-media-server-reconciliation--background-cloud-sync)
10. [Content Bridge (*Arr Automation)](#10-content-bridge-arr-automation)
11. [Multi-Tracker Hub (9 Trackers & Relays)](#11-multi-tracker-hub-9-trackers--relays)
12. [Cross-Tracker Reconciliation](#12-cross-tracker-reconciliation)
13. [Runtime Settings Hub](#13-runtime-settings-hub)
14. [Synthetic Webhook Testing](#14-synthetic-webhook-testing)
15. [Diagnostics & Webhook Debugger](#15-diagnostics--webhook-debugger)
16. [Personal Analytics & OmniWrapped](#16-personal-analytics--omniwrapped)
17. [Shared and Personal Watch Lists](#17-shared-and-personal-watch-lists)

---

## 1. Authentication & Security

Omniscrobble utilizes a multi-level access control model based on `WEBHOOK_SECRET`:

| Level | Description | How It Is Provided |
| --- | --- | --- |
| **Public** | Open read-only telemetry, PWA assets, and status feeds. | No credentials required. |
| **Webhook Secret** | Secures incoming media server webhooks from unauthorized external callers. | Query parameter `?token=<SECRET>` or HTTP Header `x-webhook-secret: <SECRET>`. |
| **Admin Authorization** | Required for state-mutating endpoints, configuration changes, backups, and live logs. | Cookie `admin_token=<SECRET>` (obtained via `/api/admin/unlock`), local admin session, query param `?token=<SECRET>`, or header `x-webhook-secret: <SECRET>`. Cookie-authenticated mutations require matching `X-CSRF-Token` and `csrf_token` cookie. |
| **Household Account** | Profile-scoped activity, analytics, and personal tracker connections. | Admin-created local account at `/login`; sessions are held in memory, expire after 12 hours, and use an HttpOnly session cookie plus CSRF cookie. |

> [!NOTE]
> If `WEBHOOK_SECRET` is not set in `.env`, the dashboard runs in local unrestricted mode and all endpoints grant administrative access automatically. Sessions utilize configurable `COOKIE_SAMESITE` policies (default: `lax`).

Local household accounts are enabled only when `WEBHOOK_SECRET` is configured. Create and manage accounts through the dashboard Accounts modal or the admin account API. Members use My account to connect their own Trakt, Simkl, AniList, and MyAnimeList profiles and can sign out from the header. Account password hashes and bounded audit records are stored under `data/`; active sessions are revoked when an account is disabled or its password changes. Members can view only activity and analytics associated with their local username and can manage tracker credentials for that profile. Global settings, household rules, reconciliation, queue operations, logs, backup/restore, and webhook replay remain admin-only.

---

## 2. Web UI & Telemetry Endpoints

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/account/login`** | `POST` | Public (rate limited) | **Household Sign In**: Verifies a local account and sets an HttpOnly session cookie and CSRF cookie. Requires `WEBHOOK_SECRET` to be configured. |
| **`/api/account/logout`** | `POST` | Household Account | **Sign Out**: Revokes the current local session and clears its cookies. |
| **`/api/account/me`** | `GET` | Household Account or Admin | **Current Identity**: Returns username, role, and authentication source; never returns password data. |
| **`/api/admin/accounts`** | `GET` | Admin | **List Household Accounts**: Returns usernames, roles, enabled state, and creation time without password hashes. |
| **`/api/admin/accounts`** | `POST` | Admin | **Create Household Account**: Creates an admin or member account with a password of at least 12 characters. |
| **`/api/admin/accounts/{username}`** | `PATCH` | Admin | **Update Household Account**: Changes role, enabled state, or password; revokes existing sessions. |
| **`/api/admin/accounts/{username}`** | `DELETE` | Admin | **Delete Household Account**: Removes an account and revokes existing sessions. |
| **`/api/automation/rules`** | `GET` | Admin | **List Event Rules**: Returns ordered rules, the default allow behavior, and precedence policy. |
| **`/api/automation/rules`** | `PUT` | Admin | **Replace Event Rules**: Validates and atomically saves the ordered rule set. Match-all rules and invalid actions or conditions are rejected. |
| **`/api/automation/rules/evaluate`** | `POST` | Admin | **Evaluate Sample Event**: Evaluates the saved rules without dispatching or persisting the sample. Returns the winning rule and conflicts. |
| **`/api/automation/rules/preview`** | `POST` | Admin | **Preview Rule Changes**: Compares current and proposed decisions for one sample event. Does not save rules or dispatch the event. |
| **`/api/automation/review`** | `GET` | Admin | **List Held Events**: Returns pending events that matched a review rule, with the rule explanation and no raw webhook payload. |
| **`/api/automation/review/{event_id}`** | `POST` | Admin | **Approve or Reject Held Event**: Accepts `{"decision":"approve"}` or `{"decision":"reject"}`. Approval reprocesses the sanitized event once while bypassing automation rules; it does not replay stored webhook data. |
| **`/`** | `GET` | Public | **Main Web Dashboard**: Renders the responsive real-time dashboard UI. |
| **`/demo`** | `GET` | Public | **Air-Gapped Demo**: Renders the dashboard in isolated demo mode with mock data. |
| **`/health`** | `GET` | Public | **Healthcheck**: Returns JSON status, authentication state, and token health metrics. |
| **`/api/health/tokens`** | `GET` | Admin | **Proactive Token Health**: Evaluates expiration windows, renewal lifespans, and push alert triggers across Trakt, Simkl, MyAnimeList, and partner accounts. |
| **`/api/health/recovery`** | `GET` | Admin | **Setup, Recovery, and Compatibility Checks**: Returns a read-only, secret-free snapshot of configuration validation, app/Python versions, local data schema versions, tracker authentication/capabilities, listener configuration, webhook authentication, offline queue, notification channels, and worker state. Reports legacy or unsupported local formats and integration versions/reachability only from explicit connection tests. Includes a 24-hour count of webhook authentication rejections by endpoint (bounded to the latest 100 attempts); no request payload, token, or client IP is retained. Loading the report does not contact external services. |
| **`/api/health/recovery/test`** | `POST` | Admin | **Explicit Integration Reachability Test**: Accepts `{"integration":"plex"}` with one of Plex, Jellyfin, Emby, Sonarr, Radarr, Overseerr, `trackers`, or `notification:<channel>` (`discord`, `telegram`, `ntfy`, `pushover`, `gotify`, `matrix`). Uses saved credentials, sends a test notification for notification channels, and returns only a redacted connected/failed result. The request makes an external connection or sends a test message only when explicitly invoked. |
| **`/metrics`** | `GET` | Public | **Prometheus Metrics**: Exposes thread-safe telemetry in Prometheus text exposition format. |
| **`/manifest.json`** | `GET` | Public | **PWA Web App Manifest**: Metadata for mobile and desktop PWA installation. |
| **`/sw.js`** | `GET` | Public | **PWA Service Worker**: Version-locked background asset cache. |
| **`/static/dashboard.css`** | `GET` | Public | **Dashboard Stylesheet**: Serves the dashboard's external responsive theme and layout styles. |
| **`/static/dashboard-theme.css`** | `GET` | Public | **Dashboard Theme Tokens**: Serves theme palettes, accent colors, and display density rules. |
| **`/static/dashboard.js`** | `GET` | Public | **Dashboard Script**: Serves the dashboard's external workspace, integration, and interaction code. |

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
| **`/api/auth/start`** | `POST` | Public | **Trakt OAuth Start**: Initiates device flow code generation for primary or partner user. |
| **`/api/auth/poll`** | `POST` | Public | **Trakt OAuth Poll**: Polls device authorization status for token exchange. |
| **`/auth/simkl`** | `GET` | Admin | **Simkl Authorization**: Dedicated portal for Simkl OAuth device PIN activation. |
| **`/auth/anilist`** | `GET` | Admin | **AniList Authorization**: Web portal for AniList token entry. |
| **`/auth/mal`** | `GET` | Admin | **MyAnimeList Authorization**: Web portal for MyAnimeList token entry. |

---

## 5. Activity Feed, Playback & System Logs

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/events`** | `GET` | Public / Admin | **Recent Activity Feed**: Returns recent scrobbles, ratings, aggregate `delivery_status` (`success`, `queued`, `failed`, `partial`, or `skipped`), Co-Watch sync statuses (`scheduled`, `success`, `queued`, `partial`, `failed`, or `skipped`), an `is_anime` boolean when known, and per-tracker `tracker_delivery` states (`success`, `queued`, `failed`, or `skipped`). Admin event records include a stable `operation_id` and `tracker_delivery_details`, with a redacted reason, outcome category, update timestamp, attempt count, and up to 10 outcome history entries per destination. Queue retries remain linked to their originating events. Public responses omit operation IDs, media payloads, raw results, and delivery details. Asynchronous sync results update the originating event. Supports optional pagination (`?limit=10&offset=0`) and returns `{ events: [...], total: N }`. |
| **`/api/events/clear`** | `POST` | Admin | **Clear Activity Feed**: Purges the recent events history from disk (`data/events.json`). |
| **`/api/events/{event_id}/retry`** | `POST` | Admin | **Retry Failed Delivery**: Requeues one failed transient queue operation associated with the activity event. Request JSON: `{"queue_item_id": 42, "confirm_duplicate_history": false}`. Authorization, configuration, and permanent client errors must be corrected before retrying. `sync_history` and `scrobble_stop` require `confirm_duplicate_history: true` because an upstream timeout can leave the original outcome uncertain. Returns the event and queue item IDs. |
| **`/api/playback`** | `GET` | Public | **Active Playback**: Returns active streams, calculated progress, and recently finished media. |
| **`/api/stats/reset`** | `POST` | Admin | **Reset Stats**: Resets lifetime counters (movies, episodes, scrobbles, ratings) in `data/stats.json`. |
| **`/api/logs`** | `GET` | Admin | **System Log Viewer**: Reads live logs from `journalctl` (or ring buffer fallback) with automatic secret redaction. Supports `?level=ALL \| ERROR \| WARNING \| INFO` and `?limit=500`. |

---

## 6. Manual Scrobbling, Standalone Players & Trakt Watchlist

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/search`** | `GET` | Admin | **Global Trakt Search**: Search movies and shows (`?query=...&type=movie \| show`). |
| **`/api/scrobble/manual`** | `POST` | Admin | **Manual History Scrobble**: Force-scrobble any movie or episode directly to Trakt, Simkl, AniList, or MAL with optional partner dual-sync. |
| **`/api/scrobble`** | `GET` | Public | **Standalone Player Bridge Info**: Service descriptor, supported standalone players (Infuse, Kodi, VLC, Stremio), payload schema, and usage examples. |
| **`/api/scrobble`** | `POST` | Webhook Secret | **Standalone Player Scrobble**: Ingests direct playback and scrobble payloads (`play`, `pause`, `stop`, `scrobble`) from standalone media players (Infuse, Kodi, VLC, Stremio) with full multi-tracker dispatch and loop suppression. Secures via Webhook Secret (`?token=` or header `x-webhook-secret`). |
| **`/api/watchlist`** | `POST` | Admin | **Trakt Watchlist**: Add a movie or show to your personal Trakt watchlist. |
| **`/api/history/remove`** | `POST` | Admin | **Unscrobble Media**: Deletes a watched history entry from Trakt and secondary trackers with optional partner unlinking. |

---

## 7. Offline Queue & Disaster Recovery

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/queue`** | `GET` | Public | **Offline Queue Status**: Returns pending queue item count and database path. |
| **`/api/queue/retry`** | `POST` | Admin | **Flush Queue**: Immediately drains and retries all pending offline items against Trakt. |
| **`/api/queue/clear`** | `POST` | Admin | **Purge Queue**: Clears all pending and failed offline items from the SQLite database. |
| **`/api/queue/prune`** | `POST` | Admin | **Prune Queue Retention**: Prunes completed and expired offline queue records older than configured retention period (default 90 days). Accepts optional JSON body `{"days": <int>}`. |
| **`/api/backup`** | `GET` | Admin | **Download Backup**: Exports a timestamped archive containing OAuth tokens, settings, SQLite queue, private watch lists, household account hashes and audit records with a versioned manifest. Sessions are not archived; restore reloads accounts and revokes active sessions. Supports AES-256-GCM encryption via `?passphrase=`, `x-backup-passphrase` header, or `CONFIG_ENCRYPTION_KEY`. |
| **`/api/backup/snapshots`** | `GET` | Admin | **List Local Backup Snapshots**: Lists retained local snapshots with filename, size, creation time, and configured retention count. |
| **`/api/backup/snapshots`** | `POST` | Admin | **Create Local Backup Snapshot**: Writes a permission-restricted snapshot under `data/backups/` and removes older snapshots beyond `BACKUP_RETENTION_COUNT` (bounded to 1–50). |
| **`/api/restore/preview`** | `POST` | Admin | **Preview Backup Restore**: Accepts a multipart archive upload and optional passphrase, validates encryption, format version, manifest, paths, file integrity, JSON files, and size limits, then reports files that would be replaced or preserved. Makes no writes. |
| **`/api/restore`** | `POST` | Admin | **Restore Backup**: Accepts a multipart archive upload with the same full validation as restore preview before any write, safe-path checks, and transparent AES-256-GCM decryption via form field `passphrase` or header. Restored watch-list data is reloaded into memory. |

---

## 8. Watch Together & Household Multi-Tenancy

| Endpoint | Method | Auth | Description |
| --- | :--- | :--- | --- |
| **`/api/cowatch`** | `GET` | Public | **Co-Watch Status**: Returns linked partner profile, whitelist of shared shows, and allowed devices. |
| **`/api/cowatch/trackers`** | `GET` | Admin | **Partner Cloud Trackers**: Returns connection and username status matrix for partner's secondary trackers (Trakt, Simkl, AniList, MAL). Supports `?demo=true`. |
| **`/api/cowatch/shows`** | `POST` | Admin | **Add Shared Show**: Adds a TV show title to `data/cowatch_shows.json`. |
| **`/api/cowatch/shows`** | `DELETE` | Admin | **Remove Shared Show**: Removes a show title from `data/cowatch_shows.json`. |
| **`/api/cowatch/devices`** | `POST` | Admin | **Add Allowed Device**: Adds a client device identifier to the Co-Watch whitelist. |
| **`/api/cowatch/devices`** | `DELETE` | Admin | **Remove Allowed Device**: Removes a client device identifier from the Co-Watch whitelist. |
| **`/api/cowatch/settings`** | `POST` | Admin | **Toggle Co-Watch Settings**: Dynamically toggles movie dual-sync (`movies: true/false`). |
| **`/api/cowatch/sync`** | `POST` | Admin | **1-Click Partner Dual Sync**: Manually pushes a watched event to your partner's Trakt profile. |
| **`/api/household/rules`** | `GET` | Public | **List Household Rules**: Returns active and configured multi-tenant routing rules (`data/household_rules.json`). Supports `?demo=true`. |
| **`/api/household/rules`** | `POST` | Admin | **Create or Update Household Rule**: Adds or updates a household routing rule based on device, media type, and show title targeting specific user profiles. Supports `?demo=true`. |
| **`/api/household/rules/{rule_id}`** | `DELETE` | Admin | **Delete Household Rule**: Removes a household routing rule by ID. Supports `?demo=true`. |
| **`/api/household/rules/{rule_id}/toggle`** | `POST` | Admin | **Toggle Household Rule**: Toggles active/disabled status of a household routing rule. Supports `?demo=true`. |
| **`/api/sonarr/shows`** | `GET` | Admin | **Live Show Autocomplete**: Queries Sonarr for series titles, excluding already whitelisted shows. |

---

## 9. Two-Way Media Server Reconciliation & Background Cloud Sync

| Endpoint | Method | Auth | Description |
| --- | :--- | :--- | --- |
| **`/api/sync/status`** | `GET` | Public | **Reconciliation Telemetry**: Connection state, active media server, and last sync timestamp. |
| **`/api/sync/settings`** | `GET` | Public | **Reconciliation Settings**: Retrieves current reconciliation server configuration, masked tokens, sync interval, and ratings sync toggles. |
| **`/api/sync/settings`** | `POST` | Admin | **Update Reconciliation Settings**: Updates media server URLs, tokens, user IDs, sync interval, and trigger startup flags with immediate background engine reload. |
| **`/api/sync/diff`** | `GET` | Admin | **Scan Discrepancies**: Compares Trakt watched history against media server library sections. Supports chunked streaming pagination via query parameters `?cursor=<int>` and `?limit=<int>` (default: 50) for memory-efficient client reconciliation. |
| **`/api/sync/reconcile/preview`** | `POST` | Admin | **Preview Reconciliation**: Scans discrepancies and returns the exact proposed actions, action counts, and title-only match warnings. The response includes a short-lived `preview_id`; this endpoint makes no changes. |
| **`/api/sync/reconcile`** | `POST` | Admin | **Execute Reconciliation**: Applies a selected or all-item batch. Supplying `preview_id` applies the exact unexpired preview snapshot; requests without it retain the legacy behavior. Mutex-protected (returns `HTTP 409 Conflict` if a sync is running or preview is expired). |
| **`/api/sync/progress`** | `GET` | Public | **Sync Progress**: Returns live percentage and progress counts for active sync batches. |
| **`/api/sync/test-connection`** | `POST` | Admin | **Test Media Server**: Validates connectivity and credentials for Plex, Jellyfin, or Emby. |
| **`/api/sync/background/status`** | `GET` | Public | **Background Cloud Sync Telemetry**: Returns automated background cloud sync status from `data/sync_state.json` (last run status, items reconciled, next scheduled run, task breakdowns). Supports `?demo=true`. |
| **`/api/sync/background/run`** | `POST` | Admin | **Trigger Cloud Sync Now**: Manually executes a full background synchronization and Letterboxd CSV export cycle. Mutex-protected (returns `HTTP 409 Conflict` if a sync is running). Supports `?demo=true`. |
| **`/api/sync/register-webhook`** | `POST` | Admin | **Auto-Register Media Server Webhook**: Automatically registers Omniscrobble's webhook endpoint in Plex (`plex.tv`), Jellyfin (Webhook plugin), or Emby (`/Webhooks`). Supports `?demo=true`. |

---

## 10. Content Bridge (*Arr Automation)

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/arr/status`** | `GET` | Public | **Content Bridge Status**: Connection health, latency, and library totals for Sonarr, Radarr, and Overseerr/Jellyseerr. |
| **`/api/arr/sync`** | `POST` | Admin | **Sync Watchlist Now**: Scans Trakt watchlist and sends missing media to Overseerr/Sonarr/Radarr. |
| **`/api/arr/test-connection`** | `POST` | Admin | **Test *Arr / Overseerr Connection**: Tests connectivity to Sonarr, Radarr, or Overseerr/Jellyseerr URL and API key. |
| **`/api/arr/lookup`** | `GET` | Admin | **Interactive *Arr Catalog Search**: Searches Sonarr series or Radarr movies and flags existing library items. Query: `type=series\|movie&term=<query>`. |
| **`/api/arr/config`** | `GET` | Admin | **Acquisition Options**: Returns configured root folders and quality profiles for Sonarr and Radarr, cached for five minutes. Includes the co-watch partner label for the admin UI. |
| **`/api/arr/add`** | `POST` | Admin + CSRF | **Add Media**: Adds a selected catalog result with folder, quality, monitoring, and search options. Optional `enable_cowatch` is strictly `false` by default, applies only to series, and requires a configured co-watch partner. |
| **`/api/ecosystem`** | `GET` | Public | **Ecosystem Status**: Aggregated health status (10/10 services) across servers, trackers, and downloaders/request managers. |

`POST /api/arr/add` request example:

```json
{
  "type": "series",
  "item_data": { "title": "Example Series", "tvdbId": 123 },
  "root_folder_path": "<configured-root-folder>",
  "quality_profile_id": 1,
  "monitored": true,
  "monitor_option": "all",
  "search_now": true,
  "enable_cowatch": false
}
```

The browser sends the matching `csrf_token` cookie value in `X-CSRF-Token`. Upstream error details are not returned because they can contain private service URLs or credentials.

---

## 11. Multi-Tracker Hub (9 Trackers & Relays)

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/trackers/status`** | `GET` | Public | **Tracker Status and Capability Matrix**: Aggregated connection, token, and health telemetry plus per-tracker supported operations (history, progress, ratings, watch lists, collection, search, live playback) and media types. |
| **`/api/trakt/status`** | `GET` | Public summary; profile details require account | **Trakt Status**: Anonymous responses contain only enabled/configured/authenticated booleans. An authenticated admin or member can retrieve details for an authorized profile. |
| **`/api/trakt/disconnect`** | `POST` | Admin | **Disconnect Trakt**: Purges local Trakt OAuth tokens (`?user=username` for partner). |
| **`/api/simkl/status`** | `GET` | Public summary; profile details require account | **Simkl Status**: Anonymous responses contain only enabled/configured/authenticated booleans. An authenticated admin or member can retrieve details for an authorized profile. |
| **`/api/simkl/pin`** | `POST` | Admin | **Request Device PIN**: Generates an OAuth device PIN for headless browser activation. Supports `?user=username` to target secondary partner profile. |
| **`/api/simkl/poll`** | `POST` | Admin | **Poll Device PIN**: Polls Simkl for user authorization confirmation. Accepts `{"user_code": "...", "user": "username"}` to persist to partner token profile. |
| **`/api/simkl/disconnect`** | `POST` | Admin | **Disconnect Simkl**: Purges local Simkl OAuth credentials. Supports `?user=username`. |
| **`/api/anilist/status`** | `GET` | Public summary; profile details require account | **AniList Status**: Anonymous responses contain only enabled/configured/authenticated booleans. An authenticated admin or member can retrieve details for an authorized profile. |
| **`/api/anilist/token`** | `POST` | Admin | **Save AniList Token**: Saves user access token for GraphQL scrobbling. Accepts `{"token": "...", "user": "username"}` to persist to partner token profile. |
| **`/api/anilist/disconnect`** | `POST` | Admin | **Disconnect AniList**: Deletes local AniList token. Supports `?user=username`. |
| **`/api/mal/status`** | `GET` | Public summary; profile details require account | **MyAnimeList Status**: Anonymous responses contain only enabled/configured/authenticated booleans. An authenticated admin or member can retrieve details for an authorized profile. |
| **`/api/mal/token`** | `POST` | Admin | **Save MAL Token**: Saves access token for MyAnimeList REST v2 API. Accepts `{"token": "...", "user": "username"}` to persist to partner token profile. |
| **`/api/mal/disconnect`** | `POST` | Admin | **Disconnect MAL**: Deletes local MyAnimeList token. Supports `?user=username`. |
| **`/api/letterboxd/status`** | `GET` | Public | **Letterboxd Status**: Telemetry, account status, and local diary statistics. |
| **`/api/letterboxd/diary`** | `GET` | Public | **Letterboxd Diary Feed**: Returns parsed local diary entries. |
| **`/api/letterboxd/export`** | `GET` | Admin | **Letterboxd Diary CSV Export**: Generates and downloads an RFC-4180 Letterboxd-compliant CSV diary export of watched movies. |
| **`/api/tmdb/status`** | `GET` | Public | **TMDb Status**: Connection and API key validity state. |
| **`/api/kitsu/status`** | `GET` | Public | **Kitsu Status**: Connection status and token health. |
| **`/api/serializd/status`** | `GET` | Public | **Serializd Status**: Connection status and token health. |
| **`/api/mdblist/status`** | `GET` | Public | **MDBList Status**: Connection health and API key status. |
| **`/api/mdblist/ratings`** | `GET` | Public | **MDBList Ratings**: Enriched community ratings for a title. |
| **`/api/relay/status`** | `GET` | Public | **Mobile Relay Status**: Connection health for mobile Trakt relays (SeriesGuide, Showly). |
| **`/api/anime/resolve`** | `GET` | Public | **Anime Resolver**: Resolves AniList GraphQL / MAL ID mapping for an anime title. |

---

## 12. Cross-Tracker Reconciliation

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/cross-sync/status`** | `GET` | Public | **Cross-Sync Status**: Reports Trakt, Simkl, and TMDb authentication state and scan progress. |
| **`/api/cross-sync/diff`** | `GET` | Admin | **Cross-Tracker Diff**: Scans Trakt–Simkl history/ratings and two-way Trakt/TMDb movie/TV ratings. TMDb matches require a shared TMDb ID. |
| **`/api/cross-sync/scan`** | `POST` | Admin | **Scan Libraries**: Triggers the configured comparisons. Trakt/TMDb rating scans can run without Simkl when both Trakt and TMDb account authentication are available. |
| **`/api/cross-sync/execute`** | `POST` | Admin | **Execute Cross-Sync**: Accepts optional `item_ids`, `direction`, and `conflict_policy` (`manual`, `trakt`, `simkl`, `tmdb`, or `newest`). Applies selected discrepancies. |
| **`/api/cross-sync/progress`** | `GET` | Public | **Cross-Sync Progress**: Real-time progress percentage for active cross-sync jobs. |
| **`/api/cross-sync/provenance`** | `GET` | Admin | **Conflict Provenance**: Returns up to 500 recent applied rating conflict decisions, including chosen source, policy, direction, rating, and timestamp. Demo mode returns an empty list. |

TMDb reads account-rated movies and TV shows through its paginated account endpoints. Reads are sequential, capped at the API's 500-page limit, and stop on request errors (including HTTP 429) so a partial scan is not treated as complete. TMDb documents a soft upper limit around 40 requests per second and asks clients to respect HTTP 429 responses; see its [rated movies](https://developer.themoviedb.org/reference/account-rated-movies), [rated TV](https://developer.themoviedb.org/reference/account-rated-tv), and [rate limiting](https://developer.themoviedb.org/docs/rate-limiting) documentation. Rating differences and one-sided ratings appear as explicit Trakt↔TMDb actions and are applied only after selection.

For rating conflicts, `manual` is the default. Bulk apply leaves conflicted rating pairs untouched; selecting both opposite actions is rejected. `trakt`, `simkl`, and `tmdb` choose the named source when that service participates in the conflict. `newest` compares per-item timestamps when both are available and refuses tied or missing timestamps. Non-conflicting history and rating actions are unaffected by this policy.

TMDb ratings sent to Trakt are rounded to the nearest integer (half values round upward) and clamped to 1–10. Comparison uses the same conversion, so an equivalent fractional TMDb rating does not create a repeating conflict. A successful choice invalidates both cached directions of that conflict; provenance records retain the source rating and the applied rating.

Applied rating conflict decisions are kept in a bounded local history at `data/cross_sync_provenance.json`, included in backups and restored with other application data. The history is limited to 500 records and is exposed only through the admin-protected provenance endpoint.

---

## 13. Runtime Settings Hub

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/settings`** | `GET` | Public / Admin | **Get Runtime Settings**: Public callers receive server/tracker enablement flags only. Admin callers receive the full settings object with credentials masked. |
| **`/api/settings`** | `POST` | Admin | **Update Runtime Settings**: Updates media server listeners, tracker states, credentials, reconciliation, arr, notifications, and rules. Notification settings accept per-event `notification_routes` with `destinations`, `severity` (`low`, `normal`, `high`, `critical`), and optional `profiles` (configured Plex usernames; empty means all profiles). |
| **`/api/settings/rules`** | `GET` | Public | **Get Scrobble Rules**: Retrieves current scrobble rules (granular episode & movie thresholds, min duration, episode duration toggle, ignored libraries, path regex patterns). |
| **`/api/settings/rules`** | `POST` | Admin | **Update Scrobble Rules**: Updates scrobble thresholds, minimum playback duration, ignored libraries, and regex patterns with automatic clamping and validation. |
| **`/api/settings/toggle`** | `POST` | Admin | **Toggle Service**: Enables/disables media server listeners or pauses/resumes trackers. |
| **`/api/settings/save-all`** | `POST` | Admin | **Save Settings Hub**: Atomically saves all Settings Hub tabs (servers, trackers, rules, notifications) to `data/settings.json` without server restarts. |
| **`/api/notifications/test`** | `POST` | Admin | **Test Notification Channel**: Dispatches a test message to a specified channel (`discord`, `telegram`, `ntfy`, `pushover`, `gotify`, `matrix`). Body: `{"channel": "discord"}`. Returns `{"ok": true}` on success. |
| **`/api/notifications/preview-route`** | `POST` | Admin | **Preview Event Route**: Sends a clearly labeled sample notification through the saved event destinations and severity without creating a media event. Body: `{"event": "scrobble"}`. Supports `?demo=true` simulation. |
| **`/api/notifications/digest`** | `POST` | Admin | **Dispatch Weekly Activity Digest**: Manually triggers immediate dispatch of the weekly activity digest across all active channels. Supports `?demo=true` simulation. |

---

## 14. Synthetic Webhook Testing

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/test/webhook`** | `POST` | Admin | **Webhook Simulator**: Simulates playback events (`media.play`, `media.pause`, `media.scrobble`), tests filter rules, inspects Co-Watch eligibility reasons, and optionally dispatches live to Trakt and partner. |

---

## 15. Diagnostics & Webhook Debugger

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/debug/webhooks`** | `GET` | Admin | **Webhook Inspector History**: Returns recently captured raw webhook payloads (up to 25 items) with sanitized tokens, endpoint provenance, and processing status. Supports `?limit=15` and `?demo=true`. |
| **`/api/debug/webhooks`** | `DELETE` | Admin | **Clear Debugger Buffer**: Purges the in-memory ring buffer of captured raw webhook events and bounded authentication-rejection counters. |
| **`/api/debug/replay`** | `POST` | Admin | **Replay & Test Payload**: Replays or tests a captured webhook payload through parser logic. Supports dry-run simulation (`dispatch: false`) or live pipeline execution (`dispatch: true`, strictly requiring admin authorization). |

---

## 16. Personal Analytics & OmniWrapped

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/analytics/summary`** | `GET` | Public | **Viewing Analytics Summary**: Returns aggregated watch metrics (cumulative high-precision watch time calculated from exact media runtimes, completed scrobbles, unique titles, completed movies/episodes, star ratings, solo vs shared co-watching ratios, and media server platform distribution). Supports `?period=all\|year\|month\|week`, `?demo=true`, and the same optional filters as `/api/analytics/export`; member accounts remain scoped to their own profile, and profile selection is admin-only. |
| **`/api/analytics/wrapped`** | `GET` | Public | **OmniWrapped Retrospective**: Computes an annual viewing celebration card including personality archetype heuristics, top binge show, co-watch breakdown, and genre telemetry. Supports `?year=YYYY` (defaults to current year), `?demo=true`, and applies partner/device privacy masking for non-admin callers. |
| **`/api/analytics/export`** | `GET` | Admin | **Export Activity or Analytics**: Downloads RFC-4180 CSV or schema-versioned JSON for activity or summary data. Optional filters include start and end date (YYYY-MM-DD), profile, media type, server, tracker, and solo/shared viewing. JSON includes schema version, generation time, timezone, timestamp semantics, filters, and records or summary. |

---

### Analytics export fields and timestamps

`/api/analytics/export` accepts `kind=activity|summary` and `format=csv|json` (defaults: activity and JSON). Date filters are inclusive and use `start_date` and `end_date` in YYYY-MM-DD format. Other filters are `profile`, `media_type`, `server`, `tracker`, and `viewing` (solo or shared).

Activity records contain `operation_id`, `timestamp`, `profile`, `server`, `action`, `title`, `media_type`, `progress`, `delivery_status`, `tracker_delivery`, `viewing`, and `player`. `tracker_delivery` maps tracker names to delivery states and is serialized as JSON inside CSV cells. Missing values are empty CSV cells or the record's JSON null/empty value. Text beginning with spreadsheet formula characters is prefixed with an apostrophe in CSV downloads.

JSON envelopes contain `schema_version` (currently 1), `generated_at` (UTC ISO-8601), `timezone` (the server's current local zone label), `timestamp_semantics`, `filters`, and either `records` or `summary`. Original activity timestamp text and offsets are preserved. Naive timestamps are interpreted as server local time; date filters compare aware timestamps after conversion to server local time. The timezone label describes the server at export time, rather than replacing the offset carried by individual records. Summary fields match `/api/analytics/summary`; summary CSV flattens that object into metric/value rows.

## 17. Shared and Personal Watch Lists

Watch lists are stored locally in `data/watch_lists.json` and remain independent from the Trakt `/api/watchlist` integration. Signed-in dashboard members can create private lists and view lists shared with them. Owners can grant `editor` or `viewer` access; admins can manage every list. Cookie-authenticated mutations require the dashboard's CSRF token. Existing lists without an owner remain admin-only until shared.

| Endpoint | Method | Auth | Description |
| --- | :---: | :---: | --- |
| **`/api/watch-lists`** | `GET` | Signed-in dashboard account | Return only the caller's private and shared lists, with `can_edit`, `can_manage`, and `access_role` flags. |
| **`/api/watch-lists`** | `POST` | Signed-in dashboard account | Create a private list owned by the caller. Body: `{"name":"Weekend picks"}`. |
| **`/api/watch-lists/{list_id}`** | `PATCH` | Owner or admin | Rename a list. Body: `{"name":"New name"}`. |
| **`/api/watch-lists/{list_id}`** | `DELETE` | Owner or admin | Delete a list and its items. |
| **`/api/watch-lists/{list_id}/access`** | `PUT` | Owner or admin | Replace list access grants. Body: `{"members":{"username":"editor"}}`; roles are `editor` or `viewer`, and targets must be enabled member accounts. |
| **`/api/watch-lists/{list_id}/automation`** | `PUT` | Owner or admin | Choose `manual`, `request`, or `acquire` for additions. Automatic request requires enabled Overseerr/Jellyseerr; automatic acquisition requires configured Sonarr or Radarr. Imported lists always use `manual`. |
| **`/api/watch-lists/{list_id}/items`** | `POST` | Owner, editor, or admin | Add a movie, TV show, or anime. Body includes `title`, `media_type`, optional `year`, `ids`, `poster_url`, up to 20 `tags`, and optional `notes` (1,000 characters maximum). Duplicate title/type/year entries in the same list are rejected. An owner-selected automatic action runs after the item is saved; its outcome is returned and persisted on the item. |
| **`/api/watch-lists/{list_id}/items/{item_id}`** | `PATCH` | Owner, editor, or admin | Update item metadata, including `tags` and `notes`. Sending an empty tags array or empty notes string clears those fields. |
| **`/api/watch-lists/{list_id}/items/order`** | `PATCH` | Owner, editor, or admin | Reorder all items. Body: `{"item_ids":["...","..."]}`; each saved item ID must appear exactly once. |
| **`/api/watch-lists/{list_id}/items/{item_id}`** | `DELETE` | Owner, editor, or admin | Remove one item. |
| **`/api/watch-lists/{list_id}/availability`** | `POST` | Signed-in list member + CSRF | Refresh and save each item's availability from configured Sonarr/Radarr libraries and Overseerr/Jellyseerr. Matching uses provider IDs; the response includes a check time and per-service states. Request-service lookups run in bounded batches. |
| **`/api/watch-lists/{list_id}/items/{item_id}/request`** | `POST` | Admin + CSRF | Request one item with its exact TMDb ID through Overseerr/Jellyseerr. Existing or pending media is skipped; approval behavior remains controlled by the request service. |
| **`/api/watch-lists/anime-search`** | `GET` | Signed-in dashboard account | Search AniList by `title` and optional `year`; returns a suggested item or `{"match":null}`. |
| **`/api/watch-lists/import/preview`** | `POST` | Admin | Validate JSON or exported TXT before applying. Body: `{"data":<JSON object or text>,"replace":false}`. Reports list/item counts and duplicates. |
| **`/api/watch-lists/import`** | `POST` | Admin | Apply validated import. `replace:false` merges items into same-name lists and adds new lists; `replace:true` replaces all lists. Duplicate items are skipped and counted. |
| **`/api/watch-lists/export?format=json\|txt`** | `GET` | Admin | Download all lists as lossless JSON or readable TXT. The dashboard also copies the TXT representation to the clipboard. |

Import and export remain admin-only and preserve the existing formats. Imported lists never transfer local ownership or access grants. TXT format uses `## List name` headings and `[movie]`, `[tv]`, or `[anime]` item lines. JSON retains IDs, ordering, tags, notes, and metadata; TXT retains list names, titles, types, years, tags, and notes while remaining able to read earlier TXT exports.

Availability snapshots are stored per item with the last check time. The request endpoint requires an exact TMDb ID and delegates approval to the configured request service. Direct Radarr/Sonarr acquisition continues through the existing catalog selection dialog.
