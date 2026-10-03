# Local Testing & Verification Guide

This guide details how to set up a local development environment for **Omniscrobble**, simulate media server webhooks from Plex, Jellyfin, Emby, Sonarr, and Radarr without needing live media playback, and run automated quality gates before submitting pull requests.

---

## 1. Quick Setup & Environment

### 1.1 Python Virtual Environment

Omniscrobble requires Python 3.10+. Create and activate a local virtual environment:

```bash
# Clone the repository
git clone https://github.com/selits/omniscrobble.git
cd omniscrobble

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 1.2 Local Configuration (`.env`)

Copy the example configuration or create a minimal `.env`:

```bash
cp .env.example .env
```

For purely local testing, Omniscrobble will run safely with defaults. If testing with authentication or protected endpoints, configure `WEBHOOK_SECRET` in your `.env`.

### 1.3 Running the Local Server

Start the FastAPI application with Uvicorn:

```bash
.venv/bin/uvicorn app.main:app --port 8000 --host 127.0.0.1
```

Once running:

- **Interactive Dashboard**: [http://localhost:8000](http://localhost:8000)
- **OpenAPI / Swagger Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Check**: [http://localhost:8000/health](http://localhost:8000/health)

---

## 2. Webhook Simulator CLI (`scripts/simulate_webhook.py`)

The Webhook Simulator crafts and dispatches realistic media server payloads to Omniscrobble's webhook endpoints. This allows testing ingestion, scrobbling, ratings, anime detection, and multi-tracker dispatch without waiting for physical media server playback.

### 2.1 Basic Usage

```bash
# Run simulator
.venv/bin/python scripts/simulate_webhook.py --server <server> --scenario <scenario> [options]
```

Or make it executable:

```bash
./scripts/simulate_webhook.py --server plex --scenario movie-finish
```

### 2.2 Supported Media Servers & Presets

| Server | Endpoint | Common Scenarios |
| :--- | :--- | :--- |
| `plex` | `/webhook` | `movie-start`, `movie-pause`, `movie-finish`, `episode-start`, `episode-finish`, `rate-movie`, `rate-episode` |
| `jellyfin` | `/webhook/jellyfin` | `movie-start`, `movie-pause`, `movie-finish`, `episode-start`, `episode-finish`, `rate` |
| `emby` | `/webhook/emby` | `movie-start`, `movie-pause`, `movie-finish`, `episode-start`, `episode-finish`, `rate` |
| `sonarr` | `/sonarr` | `download`, `test` |
| `radarr` | `/radarr` | `download`, `test` |

### 2.3 CLI Flags & Options

- `--server`: Target server (`plex`, `jellyfin`, `emby`, `sonarr`, `radarr`, default: `plex`).
- `--scenario`: Playback or action scenario (`movie-finish`, `episode-start`, `download`, etc.).
- `--title`: Custom title for the movie or episode (e.g. `"Inception"`, `"Ozymandias"`).
- `--show`: Show title for TV episodes (e.g. `"Severance"`, `"Breaking Bad"`).
- `--season`: TV season number (default: `1`).
- `--episode`: TV episode number (default: `1`).
- `--year`: Release year (e.g. `2024`).
- `--rating`: Rating value between 1 and 10 for rating scenarios (default: `10`).
- `--progress`: Playback completion percentage (0.0 to 100.0, default: `100.0`).
- `--user`: Username reported in the payload (default: `selits`).
- `--url`: Omniscrobble base URL (default: `http://localhost:8000`).
- `--token`: Webhook secret token for protected instances (`?token=<token>`).
- `--dry-run`: Output the generated payload without sending the HTTP request.
- `--verbose`, `-v`: Display full response bodies and headers.

### 2.4 Simulation Examples

#### Plex Movie Completion Scrobble

```bash
./scripts/simulate_webhook.py --server plex --scenario movie-finish --title "Dune: Part Two" --year 2024
```

#### Jellyfin Episode Playback Start

```bash
./scripts/simulate_webhook.py --server jellyfin --scenario episode-start --show "Severance" --season 2 --episode 1
```

#### Emby Rating (10/10)

```bash
./scripts/simulate_webhook.py --server emby --scenario rate-movie --title "Oppenheimer" --year 2023 --rating 10
```

#### Sonarr Download Ingestion

```bash
./scripts/simulate_webhook.py --server sonarr --scenario download --show "Lanterns" --season 1 --episode 1
```

#### Radarr Movie Acquisition

```bash
./scripts/simulate_webhook.py --server radarr --scenario download --title "Gladiator II" --year 2024
```

#### Inspect Payload with Dry Run

```bash
./scripts/simulate_webhook.py --server plex --scenario episode-finish --show "The Bear" --season 3 --episode 1 --dry-run
```

---

## 3. Local Quality Gate Runner (`scripts/test_local.sh`)

Omniscrobble includes a one-command quality gate runner that executes all required verification checks before opening a pull request or pushing to git:

```bash
./scripts/test_local.sh
```

### 3.1 Quality Gates Executed

1. **Gate 1: Git Privacy Verification**
   - Asserts that `git config user.email` is set to the GitHub privacy email (`selits@users.noreply.github.com`).
2. **Gate 2: State Isolation**
   - Verifies that no sensitive files (`.env`, `trakt_tokens.json`, `data/`) are tracked by git.
3. **Gate 3: Unit Tests**
   - Runs `.venv/bin/pytest -v` across the entire test suite. All tests must pass with 0 failures.
4. **Gate 4: Secret Scanning (Gitleaks)**
   - Runs `gitleaks detect` across repository commits using Docker or local binary to block accidental credential leaks.
5. **Gate 5: Static Demo Generation**
   - Runs `scripts/generate_static_demo.py` and verifies `docs/index.html` is generated successfully so the GitHub Pages live preview stays in sync.

---

## 4. Git Pre-Commit Hook Installation

To prevent accidental commits that break tests or leak credentials, install the pre-commit hook:

```bash
cp scripts/pre-commit-hook.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

The hook automatically runs unit tests and secret scanning before every `git commit`.

---

## 5. Security & Privacy Rules

When testing and contributing to Omniscrobble:

- **Never commit real credentials**: Do not commit media server hostnames, tokens, API keys, or personal email addresses.
- **Always use generic placeholders**: `<your-server-ip-or-domain>`, `<PORT>`, `your_trakt_client_id_here`.
- **Feature Branches**: Never commit directly to `main`. Always create a dedicated branch (`feature/...`, `fix/...`, `docs/...`).

---

## 6. Testing Tiers & Live Verification Status

To ensure complete transparency regarding test coverage, Omniscrobble categorizes all supported integrations into two distinct tiers:

### 6.1 Tier 1: Verified (Live Production)

These services are part of the maintainer's active homelab setup. They have been verified against real, live accounts and real media hardware:

| Platform | Verification Scope | Testing Method |
| :--- | :--- | :--- |
| **Plex** | Webhook ingestion, playback progress, rating sync, two-way library diffs | Live media server webhooks & daily scrobbling |
| **Trakt.tv** | OAuth device flow, scrobble dispatch, ratings, watchlist, collection sync | Live Trakt API v2 with production OAuth tokens |
| **Simkl** | OAuth device flow, simultaneous scrobble, rating sync, full sync | Live Simkl API with production OAuth tokens |
| **Sonarr & Radarr** | Trakt Watchlist auto-acquisition, download webhooks, co-watch autocomplete | Live instances with production API keys |

### 6.2 Tier 2: Community Beta (Unit-Tested & Simulated)

These services are built against official platform API specifications and tested with automated unit test suites (`tests/test_scrobbler.py`) using mocked HTTP responses and synthetic webhook simulator payloads (`scripts/simulate_webhook.py`). **They have not yet been live-tested with active user accounts in production**:

| Platform | Capability | Testing Status |
| :--- | :--- | :--- |
| **Jellyfin** | Webhook ingestion, scrobbles, ratings | Unit-tested & simulated via `simulate_webhook.py --server jellyfin` |
| **Emby** | Webhook ingestion, scrobbles, ratings | Unit-tested & simulated via `simulate_webhook.py --server emby` |
| **AniList** | GraphQL anime scrobble & progress | Unit-tested with mock GraphQL HTTP fixtures |
| **MyAnimeList** | REST v2 anime scrobble & progress | Unit-tested with mock REST HTTP fixtures |
| **Kitsu** | JSON:API v1 anime scrobble & progress | Unit-tested with mock JSON:API fixtures; not live-tested |
| **Letterboxd** | Automated CSV diary export | Unit-tested CSV formatting; awaiting user import verification |
| **Serializd** | TV diary & episode progress sync | Unit-tested with mock REST fixtures; not live-tested |
| **TMDb** | Watchlist & star rating sync | Unit-tested with mock TMDb v3/v4 fixtures; not live-tested |
| **MDBList** | Aggregated critic & user ratings | Unit-tested with mock API fixtures; not live-tested |
| **SeriesGuide / Showly** | Trakt mobile sync relay | Unit-tested Trakt relay logic |

> 💬 **Feedback & Bug Reports**: If you use any Community Beta integrations with live accounts, please share feedback or submit PRs/issues on GitHub. Your logs and real-world results help move platforms into Tier 1!
