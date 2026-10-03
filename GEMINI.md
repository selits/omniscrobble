# Project Guidelines & Agent Instructions

This document defines the architectural rules, security boundaries, and development workflows for `omniscrobble` (formerly `plex-trakt-webhook`). All AI agents and contributors must adhere to these instructions.

---

## 1. Privacy & Security Rules (CRITICAL)

- **Git Author Identity:**
  - Every commit must use the GitHub privacy email:
    `selits <selits@users.noreply.github.com>`
  - Never commit using a personal email address.
- **Scrubbing Personal Infrastructure:**
  - Never commit real mediaserver/server hostnames, personal domain names, assigned high-range ports, or user credentials.
  - Always use generic documentation placeholders: `<your-server-ip-or-domain>`, `<PORT>`, `your_trakt_client_id_here`.
  - Personal setup guides and credentials belong in the user's local Obsidian vault, **not** in this repository.
- **Sensitive Files & State Isolation:**
  - Never track `.env`, `*.env`, `trakt_tokens.json`, or anything in the `data/` directory (`data/settings.json`, `data/stats.json`, `data/events.json`, `data/playback_sessions.json`, `data/anime_cache.json`, `data/tokens/*.json`, `data/queue.db`, `data/cowatch_shows.json`).
  - Always verify `.gitignore` before adding new files.
- **Dashboard & Screenshot Privacy Shielding:**
  - Non-admin dashboard views must shield private information: mask Plex usernames (`mask_username`), partner accounts (`@●●●●●●●●`), server ports, hostnames, and webhook secret tokens.
  - Emitted service log lines must pass through `log_mgr.sanitize_line()` to ensure query parameters (`?token=...`) and Bearer tokens are redacted.

---

## 2. Git Branching & Workflow (CRITICAL)

- **Explicit User Permission Required for Commits & Pushes:**
  - NEVER execute `git commit` or `git push` autonomously.
  - Always present the proposed commit message, diff summary, and changed files to the user, and wait for their explicit confirmation before committing or pushing.
- **No Direct Commits to Main/Master:**
  - Never commit or push directly to `main` (or `master`).
  - Always work in a dedicated branch (e.g., `feature/...`, `docs/...`, `fix/...`).
- **Workflow & Quality Gate:**
  - Create a new branch branched from up-to-date `main`.
  - Develop changes and verify all unit tests pass (`.venv/bin/pytest`).
  - Only merge into `main` after verification is complete.
- **Static Demo Synchronization (`docs/index.html`):**
  - Whenever modifying `app/templates/dashboard.html` or `app/services/demo_manager.py`, always run `.venv/bin/python scripts/generate_static_demo.py` so the live GitHub Pages preview remains in sync.
- **Brand & Social Asset Generation:**
  - When modifying logos, taglines, or platform badges, update `scripts/generate_logo_assets.py` and run `.venv/bin/python scripts/generate_logo_assets.py` to regenerate `banner.*`, `social-preview.*`, and `favicon.*`.
- **Version Bumping & Release Protocol:**
  - Adhere strictly to Semantic Versioning (`MAJOR.MINOR.PATCH`).
  - When releasing, bump `APP_VERSION` in `app/main.py`.
  - Update version assertion in `tests/test_scrobbler.py:test_dashboard_footer_and_repo_link`.
  - Annotated git tags (`vX.Y.Z`) are cut on `main` only after full test verification.
- **Pull Request Automation (`gh` CLI):**
  - When the user explicitly requests to commit, push, and create a PR, execute the full pipeline autonomously without intermediate stops.
  - In non-interactive subshell environments, dynamically source `GH_TOKEN` from the git credential helper:

    ```bash
    GH_TOKEN=$(printf "protocol=https\nhost=github.com\n" | git credential fill 2>/dev/null | grep '^password=' | cut -d= -f2) gh pr create ...
    ```

---

## 3. Python Environment & Testing

- **Virtual Environment:**
  - The project uses a local virtual environment located at `.venv/`.
  - Python version: 3.10+.
- **Dependencies:**
  - Managed in `requirements.txt`.
- **Test Protocol:**
  - Always run unit tests with:

    ```bash
    .venv/bin/pytest
    ```

  - All unit tests in `tests/test_scrobbler.py` must pass with 0 failures before completing any code changes or committing.
  - When adding new features or endpoints, add corresponding unit tests using `pytest` and `httpx.ASGITransport` / `starlette.testclient`.
- **Secret Detection & Leak Prevention Protocol:**
  - Before proposing any commit or pushing, always verify that zero secrets, personal infrastructure hostnames, or tokens are leaked using `gitleaks`:

    ```bash
    docker run --rm -v $(pwd):/path zricethezav/gitleaks:latest detect --source="/path" --verbose
    ```

    or locally via `gitleaks detect`.
  - The GitHub Actions CI workflow (`.github/workflows/ci.yml`) runs `gitleaks-action` on every push and PR to block accidental secret leaks.

---

## 4. Architecture & Code Conventions

- **Asynchronous Network I/O:**
  - FastAPI async route handlers must **never** make blocking network requests.
  - All external requests to Trakt, Plex, or third-party APIs must use `httpx.AsyncClient`.
  - Share the `httpx.AsyncClient` instance across requests with proper FastAPI lifespan startup and shutdown cleanup.
- **Trakt API Resilience:**
  - **Proactive Token Refresh:** Check `created_at` and `expires_in`; refresh tokens within 24 hours of expiration.
  - **401 Retry:** If a request receives a 401 Unauthorized, refresh the OAuth token and retry the request once.
  - **429 Rate Limiting:** Handle Trakt rate limits with exponential backoff and honor the `Retry-After` header.
  - **TV Show Disambiguation:** Extract remake/release years from title strings (e.g., `Show Name (2024)` → title: `Show Name`, year: `2024`) to ensure Trakt maps to the correct show ID.
- **Offline Queue Integration (`queue_manager.py`):**
  - All external synchronization calls to Trakt (`sync_history`, `sync_ratings`, `sync_collection`, `sync_watchlist`) must handle transient errors via `is_temporary_error()` and call `queue_mgr.enqueue(...)`.
  - Any new event type must have a corresponding execution branch in `queue_manager.py:process_queue()`.
- **Watch Together & Multi-User Engine (`cowatch_manager.py`, `user_manager.py`):**
  - Multi-user tokens must be stored in isolated files (`data/tokens/{username}_tokens.json`).
  - `.env` `CO_WATCH_SHOWS` entries must automatically merge with dynamic dashboard entries in `data/cowatch_shows.json` on startup without overwriting.
  - Always return structured eligibility tuples `(eligible: bool, reason: str)` from `check_cowatch_eligibility()` for transparent activity logs.
- **Media Server Ingestion Defaults (`settings_manager.py`):**
  - All media server webhook listeners (`Plex`, `Jellyfin`, `Emby`) MUST be **disabled by default** on clean installations.
  - Upgrades must detect and preserve existing active servers without activating unused ones.
  - Disabled server listeners must fast-bypass incoming webhook processing immediately.
- **Failure Alert Throttling (`notifier.py`):**
  - When upstream tracker API requests fail (5xx or rate limits) and `NOTIFY_ON_FAILURE` is active, alerts must pass through an in-memory deduplication cooldown (minimum 30 minutes per title) to avoid flooding notification channels.
- **UI Flex Layout & Mobile Safety:**
  - Multi-button action rows (such as card headers, modal footers, and table action cells) must use `display: flex; flex-wrap: wrap; gap: 6px;` rather than `nowrap` or fixed widths to prevent buttons from overflowing or crowding adjacent cells on mobile displays.
- **Admin Authorization & Security Gates:**
  - State-mutating endpoints (`POST`, `DELETE`) and sensitive telemetry endpoints (`/api/logs`, `/api/backup`, `/api/restore`) must check `is_admin_request(request)`.
  - Webhook endpoints must support `Config.WEBHOOK_SECRET` via query parameter `?token=` or header `x-webhook-secret`.
  - Limit media scrobbling to `Config.PLEX_ALLOWED_USERS` when configured.
- **UI & Mobile Responsiveness Conventions:**
  - All dashboard templates (`app/templates/dashboard.html`, `app/templates/auth.html`) must remain fully responsive on mobile screens (`@media (max-width: 640px)`).
  - Use fluid layouts, touch scrolling (`-webkit-overflow-scrolling: touch`), and touch-friendly button targets.
- **Uvicorn File-Watching Safety:**
  - Do not enable `reload=True` in production or default environments. Saving tokens to disk must not cause Uvicorn to reboot and drop active requests.

---

## 5. Deployment & Operations

- **Auto-Start on Reboot:**
  - Systemd user service defined in `plex-trakt.service` (or `omniscrobble.service`).
  - Shell startup wrapper in `start.sh` (must maintain executable permissions `+x`).
  - Uses `%h/omniscrobble` (or `%h/plex-trakt-webhook`) for portable user-home expansion.
- **Automated Upgrades:**
  - Upgrade wrapper in `upgrade.sh` pulls latest `main`, updates dependencies, enables lingering, and reloads systemd user service.
- **Docker & Containerization:**
  - Multi-stage / hardened `Dockerfile` running as non-root user `appuser`.
  - Persistent data mounted at `./data:/app/data`.
  - Includes a built-in Docker `HEALTHCHECK` targeting `/health`.
- **Continuous Integration & Automated Secret Scanning:**
  - GitHub Actions workflow defined in `.github/workflows/ci.yml`.
  - Runs automated Gitleaks secret scanning on all commits, branches, and PRs.
  - Runs linting and `pytest` on push and pull requests.
