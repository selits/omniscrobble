# Agent Guidelines & Instructions

All autonomous and pair-programming AI agents (Gemini, Claude, Copilot, Cursor, Codex, etc.) working on this repository must strictly adhere to the project rules and security boundaries documented in:

- [**`GEMINI.md`**](./GEMINI.md)

## Key Rules Summary (CRITICAL)

1. **Git Author Identity:**
   - Every commit must use the GitHub privacy email:
     `selits <selits@users.noreply.github.com>`
   - Never commit using a personal email address.
2. **Explicit Confirmation Required for Commits & Pushes:**
   - NEVER execute `git commit` or `git push` autonomously without explicit user confirmation.
   - Always present the proposed commit message, diff summary, and changed files to the user and wait for approval.
3. **No Direct Commits to Main:**
   - Always develop in dedicated feature or fix branches (`feature/...`, `fix/...`, `docs/...`).
4. **Scrub Personal Infrastructure:**
   - Never commit real mediaserver/server hostnames, personal domain names, high-range ports, passwords, or personal credentials.
   - Always use generic placeholders: `<your-server-ip-or-domain>`, `<PORT>`, `your_trakt_client_id_here`.
5. **Quality Gates & Secret Detection:**
   - Always run unit tests with `.venv/bin/pytest`. All tests must pass with 0 failures before proposing any commit.
   - Always verify zero secret leaks using `gitleaks detect` (or `docker run --rm -v $(pwd):/path zricethezav/gitleaks:latest detect --source="/path"`) before proposing any commit.
6. **Documentation & Demo Synchronization:**
   - Whenever dashboard templates (`app/templates/dashboard.html`) or mock catalog data change, always regenerate the static demo via `.venv/bin/python scripts/generate_static_demo.py` so GitHub Pages remains in sync.
7. **Server Listener Defaults:**
   - All media server listeners (Plex, Jellyfin, Emby) must remain disabled by default on clean installations. Upgrades must detect and preserve existing active servers without activating unused ones.
8. **Pull Request Automation (`gh` CLI):**
   - When the user instructs to commit, push, and create a pull request, execute the entire pipeline end-to-end without pausing to ask again.
   - For `gh pr create` in subshell environments, dynamically source the GitHub token from the local git credential helper:
     `GH_TOKEN=$(printf "protocol=https\nhost=github.com\n" | git credential fill 2>/dev/null | grep '^password=' | cut -d= -f2)`

