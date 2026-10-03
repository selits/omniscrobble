# Omniscrobble Developer & Contributor Guide

Welcome to Omniscrobble development! This document provides guidelines, environment setup instructions, and testing workflows for contributors and agents.

---

## 1. Quick Links

- [**Local Testing & Webhook Simulator Guide**](./docs/LOCAL_TESTING.md) — Setup, Webhook Simulator CLI, quality gate runner.
- [**System Architecture & Directory Map**](./docs/ARCHITECTURE.md) — Technical layers, data flow, and components.
- [**API Documentation**](./docs/API.md) — Endpoints, payloads, and authentication.
- [**Configuration Guide**](./CONFIGURATION.md) — Environment variables and runtime settings.
- [**Agent Guidelines**](./AGENTS.md) & [**Project Instructions**](./GEMINI.md) — Security boundaries and Git rules.

---

## 2. Environment Setup

Omniscrobble uses Python 3.10+ and a local virtual environment:

```bash
# Set up virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Install Git pre-commit hook (runs unit tests and Gitleaks)
cp scripts/pre-commit-hook.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

---

## 3. Local Testing & Webhook Simulation

You do not need an active media server to test changes. Omniscrobble provides a local webhook simulator CLI:

```bash
# Simulate a Plex movie finish scrobble
./scripts/simulate_webhook.py --server plex --scenario movie-finish --title "Inception" --year 2010

# Simulate a Jellyfin episode playback start
./scripts/simulate_webhook.py --server jellyfin --scenario episode-start --show "Severance" --season 2 --episode 1

# Simulate a Sonarr episode download
./scripts/simulate_webhook.py --server sonarr --scenario download --show "Lanterns" --season 1 --episode 1
```

For complete CLI options and simulation scenarios, see [**`docs/LOCAL_TESTING.md`**](./docs/LOCAL_TESTING.md).

---

## 4. Quality Gates & Verification

Before submitting pull requests or proposing commits, execute the unified local quality gate runner:

```bash
./scripts/test_local.sh
```

This script verifies:

1. **Git Privacy Verification**: Asserts that `git config user.email` uses the privacy email `selits@users.noreply.github.com`.
2. **State Isolation**: Asserts no `.env` or files under `data/` are tracked by git.
3. **Unit Tests**: Runs `.venv/bin/pytest -v` (0 failures allowed).
4. **Secret Scanning**: Runs Gitleaks across git history.
5. **Static Demo Validation**: Regenerates and validates `docs/index.html`.

---

## 5. Security & Git Guidelines

1. **Git Author Identity**:
   - Every commit must use: `selits <selits@users.noreply.github.com>`
   - Never commit using personal email addresses.
2. **No Direct Commits to Main**:
   - Always work in dedicated branches: `feature/...`, `fix/...`, `docs/...`.
3. **Scrub Personal Infrastructure**:
   - Never commit real server IPs, personal domains, high ports, or tokens.
   - Use placeholders: `<your-server-ip-or-domain>`, `<PORT>`, `your_client_id_here`.
4. **Static Demo Synchronization**:
   - Whenever dashboard templates (`app/templates/dashboard.html`) change, regenerate the static demo:

     ```bash
     .venv/bin/python scripts/generate_static_demo.py
     ```

---

## 6. Testing Tiers & Live Verification Transparency

Omniscrobble maintains a strict and transparent distinction between integrations that are live-tested in active production versus those that are unit-tested and simulated:

- **Tier 1 (Verified / Daily Driver)**:
  - **Platforms**: Plex, Trakt.tv, Simkl, Sonarr, Radarr.
  - **Scope**: Continuously tested and verified in the maintainer's primary homelab against physical media playback and real user account APIs.
- **Tier 2 (Community Beta / Mock Tested)**:
  - **Platforms**: Jellyfin, Emby, AniList, MyAnimeList, Kitsu, Letterboxd, Serializd, TMDb, MDBList, and SeriesGuide/Showly.
  - **Scope**: Implemented to official vendor API specifications and rigorously verified via 194 unit tests and synthetic webhook simulations (`scripts/simulate_webhook.py`). **Not yet live-tested with active user accounts in production**.

For complete details on testing scenarios and simulator flags, consult [**`docs/LOCAL_TESTING.md`**](./docs/LOCAL_TESTING.md).
