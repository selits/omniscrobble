# Project Guidelines & Agent Instructions

This document defines the architectural rules, security boundaries, and development workflows for `plex-trakt-webhook`. All AI agents and contributors must adhere to these instructions.

---

## 1. Privacy & Security Rules (CRITICAL)

- **Git Author Identity:**
  - Every commit must use the GitHub privacy email:
    `selits <selits@users.noreply.github.com>`
  - Never commit using a personal email address.
- **Scrubbing Personal Infrastructure:**
  - Never commit real mediaserver hostnames (`*.example.com`), personal server names, assigned high-range ports, or user credentials.
  - Always use generic documentation placeholders: `<your-server-ip-or-domain>`, `<PORT>`, `your_trakt_client_id_here`.
  - Personal setup guides and credentials belong in the user's local Obsidian vault, **not** in this repository.
- **Sensitive Files:**
  - Never track `.env`, `*.env`, `trakt_tokens.json`, or anything in the `data/` directory.
  - Always verify `.gitignore` before adding new files.

---

## 2. Git Branching & Workflow (CRITICAL)

- **No Direct Commits to Main/Master:**
  - Never commit or push directly to `main` (or `master`).
  - Always work in a dedicated branch (e.g., `feature/...`, `docs/...`, `fix/...`).
- **Workflow & Quality Gate:**
  - Create a new branch branched from up-to-date `main`.
  - Develop changes and verify all unit tests pass (`.venv/bin/pytest`).
  - Only merge into `main` after verification is complete.

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
- **Webhook Security & Configuration:**
  - Support optional webhook authentication via `Config.WEBHOOK_SECRET` (accepting either query param `?token=` or header `x-webhook-secret`).
  - Allow user filtering via `Config.PLEX_ALLOWED_USERS`.
- **Uvicorn File-Watching Safety:**
  - Do not enable `reload=True` in production or default environments. Saving tokens to disk must not cause Uvicorn to reboot and drop active requests.

---

## 5. Deployment & Operations

- **Auto-Start on Reboot:**
  - Systemd user service defined in `plex-trakt.service`.
  - Shell startup wrapper in `start.sh` (must maintain executable permissions `+x`).
  - Uses `%h/plex-trakt-webhook` for portable user-home expansion.
- **Docker & Containerization:**
  - Multi-stage / hardened `Dockerfile` running as non-root user `appuser`.
  - Persistent data mounted at `./data:/app/data`.
  - Includes a built-in Docker `HEALTHCHECK` targeting `/health`.
- **Continuous Integration:**
  - GitHub Actions workflow defined in `.github/workflows/ci.yml`.
  - Runs linting and `pytest` on push and pull requests.
