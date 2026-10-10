# Application Bloat and Maintainability Review

**Review status:** Discovery and Stage 1 complete; Stage 2 is the next proposed step.

## Summary

Omniscrobble has a broad feature surface, but the main maintenance cost is concentrated in a few places: application orchestration remains centralized in `app/main.py`, most API tests remain in one large module, and the production Docker image installs browser and test dependencies. Generated dashboard files add repository bytes, but they are built outputs needed by the app and GitHub Pages; removing them would move complexity into deployment without reducing maintained source.

There is not enough feature-level usage evidence to recommend retiring integrations. The project has local request and scrobble counters, but they do not report adoption by tracker or feature across installations. Keep the current feature set until the maintainer identifies unwanted support burden or a privacy-preserving way to assess local use.

## Evidence and hotspots

| Area | Evidence | Maintenance cost | Review finding |
| --- | --- | --- | --- |
| Application orchestration | `app/main.py` is now 7,014 lines, with 247 top-level functions/classes and 79 direct route decorators. It owns webhook ingestion, event processing, dashboard APIs, background workers, and feature orchestration. | Changes to unrelated flows still share imports and orchestration in one large file. | Activity delivery classification and persistence now live in `app/services/activity_delivery.py`; continue with one cohesive boundary at a time. |
| Route modules | `app/routes/` has five modules totaling 167 lines. `recovery.py` contains its handlers; accounts, integrations, reconciliation, and watch-list modules register handlers looked up by name from `globals()` in `app/main.py`. | HTTP declarations are separated, but most behavior and dependencies remain centralized and implicit. | Continue modularization by moving behavior with explicit dependencies, not by adding more declaration-only wrappers. |
| API test concentration | The former `tests/test_scrobbler.py` contained 12,998 lines (about 83% of Python test code) and 282 tests. Those tests now live in 21 focused domain modules. Common HTTP client and server settings setup lives in `tests/helpers.py`. | The monolith made finding and reviewing tests harder and encouraged duplicated setup. | Stage 2 split the tests by behavior, retained test names/assertions, and centralized shared setup. |
| Production dependency set | Before this change, `requirements.txt` had 11 entries. `pytest`, `pytest-asyncio`, and `playwright` are test tools, yet the Dockerfile installed every entry. A repository search found no direct application, script, or test imports of `requests`. | Runtime installed test packages and one apparently unused direct dependency. | Stage 1 separates runtime and development requirements and removes `requests`. The image builds, app imports in the runtime image, and `pytest`, Playwright, and Requests are absent there. `pip-audit` reports no known vulnerabilities for the development dependency set. |
| Dashboard source and generated output | Dashboard JavaScript is maintained as 18 modules under `app/static/dashboard_modules/`; `scripts/build_dashboard_bundle.py` builds `app/static/dashboard.js`. The app bundle and `docs/assets/dashboard.js` are identical at 413,950 bytes. `docs/index.html` is a generated 383,662-byte standalone demo. | Generated copies increase repository size and require synchronization. | Keep these artifacts while the app serves the bundle and GitHub Pages serves the demo. Improve generation checks; do not count generated copies as source complexity or edit them directly. |
| Sync service names and responsibilities | `multi_tracker.py` handles real-time tracker dispatch; `cross_tracker_sync.py` reconciles tracker histories/ratings; `reverse_sync_manager.py` reconciles media-server libraries with Trakt; `cloud_sync_manager.py` schedules multiple background jobs. | Similar “sync” names can obscure direction and ownership, but the initial review did not find these to be interchangeable implementations. | Clarify ownership and direction in architecture docs before considering consolidation. |
| Integration support | The client directory contains 15 vendor-specific clients plus the shared MediaBrowser base client. The feature guide covers trackers, servers, notifications, automation, analytics, lists, and recovery. | Every enabled integration adds authentication, compatibility, tests, and support paths. | No feature retirement recommendation without usage and support evidence. |
| Architecture manifest accuracy | The architecture inventory and directory tree listed `app/clients/betaseries_client.py`, but that file and any other BetaSeries references do not exist. | Stale manifests make it harder to tell which code is real and maintained. | Corrected the manifest during this review by removing the phantom entry. |

## Usage evidence limits

`app/metrics.py` keeps request counts by endpoint and aggregate scrobble, rating, collection, and Co-Watch counts. It does not identify tracker-specific or feature-specific adoption. The repository contains no representative multi-installation usage dataset. Runtime configuration and history are installation-local and may contain private viewing details, so this review did not inspect the ignored `data/` directory.

As a result, the review can rank engineering cost but cannot responsibly decide that a tracker or user-facing feature is unused. Any later pruning proposal should be based on the maintainer's explicit needs and, where relevant, their own local configuration or support history.

## Feature and integration map

| User-facing area | Current integrations or capability | Shared implementation | Overlap and support considerations | Usage evidence available in the repository |
| --- | --- | --- | --- | --- |
| Real-time tracking and diaries | Trakt, Simkl, TMDb, AniList, MyAnimeList, Kitsu, Letterboxd, Serializd, MDBList | `multi_tracker.py` and its capability registry for dispatch and tracker availability | Tracker capabilities differ; anime progress, diaries, and watchlists do not have identical semantics. Avoid assuming every client can be replaced by one generic tracker. | Aggregate dispatch/scrobble counters exist, but there are no per-tracker adoption counts across installations. |
| Media-server ingestion and reconciliation | Plex, Jellyfin, Emby | Shared MediaBrowser API base for Jellyfin/Emby; separate Plex API client | The webhook parsers and media-server APIs share some concepts, but server-specific payloads and auth remain real differences. | Request totals are not broken out by server integration. No representative usage dataset is included. |
| Acquisition | Sonarr, Radarr, Overseerr/Jellyseerr | `arr_bridge.py` with watch-list and automation integrations | Watch-list auto-grab and manual acquisition meet related workflows; document their handoff and ensure there is one obvious user entry point. | No per-provider adoption counters; available state is installation-local. |
| Notifications | Discord, Telegram, ntfy, Pushover, Gotify, Matrix | `notifier.py`; scheduled reports use `digest_manager.py` | Channels have different configuration and delivery rules, but share dispatch, preferences, and failure handling. | No channel-level delivery or adoption breakdown is exported. |
| Local workflows | Household sharing, reconciliation, retry queue, watch lists, automation rules, analytics, backup/restore, dashboards | Focused service managers, with several route handlers still in `main.py` | These are product workflows rather than interchangeable integrations. Their configuration and operational support burden should be assessed separately. | Generic route and outcome counters exist; they cannot establish adoption across users. |

## Proposed staged reduction plan

| Stage | Change | Benefit | Effort | Risk and guardrail |
| --- | --- | --- | --- | --- |
| 1. Reduce runtime install surface — complete | Split runtime dependencies from `requirements-dev.txt`; Docker installs runtime dependencies, while CI and local setup install the development set. `requests` was removed after a repository-wide import check. | Smaller production environment and clearer separation between app and test tooling. | Low | Verified by a successful runtime image build/import check, absence of test packages from the image, clean `pip-audit`, and 405 passed tests (28 skipped). |
| 2. Split the API test monolith — complete | Moved all 282 tests from `tests/test_scrobbler.py` into 21 domain-focused modules covering analytics/debugging, anime and tracker integrations, acquisition, Co-Watch/households, dashboards, notifications, playback, queue delivery, reverse sync, settings/security, watch lists, and webhooks. Consolidated shared setup in `tests/helpers.py`. | Easier navigation, ownership, and focused local test runs; less merge contention. | Medium | Full suite passes with test names and assertions preserved. Queue collection retry now uses a temporary database to avoid depending on stale shared queue state. |
| 3. Move behavior behind real domain boundaries — complete | Extracted delivery classification, redaction, status aggregation, and event outcome persistence from `app/main.py` into `ActivityDeliveryManager`. Its event store, persistence callback, queue, log manager, and notifier are explicit dependencies. The existing HTTP endpoints and response contracts remain in place. | Reduced orchestration size and a focused, directly testable delivery boundary. | High | Focused service tests and the full suite pass; endpoint, privacy, queue linkage, and delivery behavior are covered. |
| 4. Reassess feature support only with evidence | Review integration upkeep against actual maintainer priorities and support burden before considering a retirement or replacement. | Potentially reduces long-term support work if a feature is truly unwanted. | Unknown | Do not remove features based on directory size, test count, or absent cross-installation telemetry. Require a specific decision and migration impact review. |

## Not recommended in this review

- Do not delete generated dashboard bundles while the server and Pages demo depend on them.
- Do not combine the real-time, cloud-to-cloud, and media-server reconciliation services solely because their names contain “sync.”
- Do not rewrite the application or retire integrations as part of discovery.
- Do not add broad behavioral analytics just to justify pruning. If feature-use evidence is needed, first assess whether installation-local counts can answer the question without collecting titles, usernames, or cross-installation data.

## Suggested first implementation

Stages 1–3 are complete. Stage 4 remains an evidence-based review of feature support. The current review found no representative cross-installation usage data, so it recommends keeping integrations until maintainers identify a specific support burden or product priority to assess.
