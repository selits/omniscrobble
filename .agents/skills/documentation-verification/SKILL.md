---
name: documentation-verification
description: >-
  Audits and synchronizes all project documentation, architecture blueprints,
  REST API specifications, configuration manuals, and static demos before
  proposing any git commits or closing feature tasks. Use this skill whenever
  code changes are made, before committing, or when asked to verify documentation.
---

# Documentation Verification & Synchronization Skill

This skill ensures that project documentation never drifts out of sync with code changes, architecture refactorings, or configuration updates.

## When to Run

Execute this verification procedure:
1. **Before Proposing Any Commit**: Whenever code, endpoints, database pragmas, models, services, or configuration variables are modified.
2. **Before Bumping Versions or Creating Releases**: To ensure release notes, badges, metrics, and documentation tables match the release.
3. **When Auditing Repository Health**: When the user requests a documentation review or status check.

---

## Verification & Synchronization Checklist

### 1. REST API Specification (`docs/API.md`)
- [ ] Inspect any new, modified, or deleted route handlers in `app/main.py`.
- [ ] Ensure all new routes are documented in the appropriate functional section of `docs/API.md`.
- [ ] Verify HTTP method, authentication tier (Public, Webhook Secret, Admin), request body schema, and response format.
- [ ] Count total documented endpoints and update high-level metrics in `README.md` and `docs/ARCHITECTURE.md`.

### 2. Architecture Blueprint & Directory Manifest (`docs/ARCHITECTURE.md`)
- [ ] **Component Layers**: Check that new service engines, parsers, or API clients are described in their appropriate functional layer (Sections 1–7):
  - Ingestion & Routing (`app/main.py`)
  - Normalization & Parsing (`app/plex_parser.py`, `app/jellyfin_parser.py`, `app/emby_parser.py`)
  - Business Logic & Dispatch (`app/services/multi_tracker.py`, `cowatch_manager.py`, etc.)
  - Client Layer (`app/clients/*`)
  - Persistence & Offline Queue (`app/services/atomic_writer.py`, `queue_manager.py` with SQLite WAL mode, `settings_manager.py`, `user_manager.py`)
  - Background Workers (`cloud_sync_manager.py`, `reverse_sync_manager.py`, `arr_bridge.py`, `notifier.py`)
  - Observability & UI (`dashboard_renderer.py`, `dashboard.html`, `log_manager.py`, `metrics.py`)
- [ ] **Directory Manifest**: Ensure every newly added or removed file in `app/` is reflected in the codebase directory tree manifest.

### 3. Configuration Reference (`CONFIGURATION.md` & `.env.example`)
- [ ] Inspect `app/config.py` for any new or modified environment variables or default value changes.
- [ ] Ensure every new variable is added to `.env.example` with clear comments.
- [ ] Ensure new variables are explained in their topical guide section in `CONFIGURATION.md`.
- [ ] **Exhaustive Reference Table**: Ensure new variables are added to **Section 7: Complete Environment Variable Reference** in `CONFIGURATION.md` with:
  - Exact variable name
  - Default value
  - Data type
  - Runtime Editable status (Yes / No)
  - Clear description

### 4. Feature Guides (`docs/FEATURES.md`) & `README.md`
- [ ] If a major feature or integration is introduced or modified (e.g. Co-Watch, Two-Way Reconciliation, Content Bridge, Multi-Tracker Hub, Offline Queue, Notifications), ensure `docs/FEATURES.md` and `README.md` reflect the full current scope.
- [ ] Audit headers and badges to ensure legacy single-tracker or single-server terminology is modernized (holistic architecture rule).

### 5. Static Demo Synchronization (`docs/index.html`)
- [ ] If `app/templates/dashboard.html` or `app/services/demo_manager.py` was modified:
  - Run `.venv/bin/python scripts/generate_static_demo.py`
  - Verify `docs/index.html` is regenerated and healthy so GitHub Pages stays in sync.

### 6. Markdown Formatting & Linting
- [ ] Always execute the markdown linter before proposing a commit:
  ```bash
  ./scripts/lint_markdown.sh
  ```
  If formatting issues exist, run:
  ```bash
  ./scripts/lint_markdown.sh --fix
  ```
- [ ] Ensure 0 linting errors before proceeding to commit.
